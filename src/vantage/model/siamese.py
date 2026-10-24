"""Сиамская сеть: классификация не объекта, а изменения.

Почему сиамская, а не обычная
-----------------------------
Обычный подход: свёрточная сеть смотрит на снимок «после» и отвечает
«свалка / не свалка». Проблема в том, что свалка на 10-метровом пикселе
выглядит как серо-бурое пятно — ровно как карьер, отвал грунта, грунтовая
площадка и распаханное поле. Чтобы различить их по одному снимку, сети
нужны десятки тысяч размеченных примеров, которых у нас нет.

Сиамская схема ставит другую задачу. Один и тот же энкодер (общие веса)
кодирует состояние **до** и **после**, а решение принимается по разнице
представлений. Сеть учится не «как выглядит свалка», а «как выглядит
превращение в свалку». Это:

  * снимает необходимость учить внешний вид фона — он одинаков в обеих
    эпохах и в разности сокращается;
  * даёт работоспособность на сотнях примеров вместо десятков тысяч;
  * делает признаки интерпретируемыми — разность представлений можно
    разложить по вкладу каналов (см. :mod:`vantage.explain`).

Голова классификатора
---------------------
На вход подаётся не только разность ``|f_after − f_before|``, но и сами
представления. Разность отвечает на вопрос «насколько сильно изменилось»,
а исходные представления — «из чего во что». Карьер и свалка могут дать
похожую величину изменения, но принципиально разные исходные состояния:
карьер возникает на нетронутой земле, свалка — обычно у существующей
грунтовой дороги.

Энкодер написан вручную, а не взят готовым
------------------------------------------
Осознанное решение. Готовый ResNet-50 из библиотеки дал бы, возможно,
чуть лучше метрику, но на вопрос «объясните, что делает третий блок вашей
сети» ответить было бы нечем. Здесь каждый слой можно показать пальцем.
Архитектура — компактная сеть с остаточными связями: четыре стадии,
удвоение числа каналов и уменьшение разрешения вдвое на каждой.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SiameseConfig:
    """Гиперпараметры архитектуры."""

    in_channels: int
    embedding_dim: int = 128
    stem_channels: int = 32
    n_stages: int = 4
    dropout: float = 0.3

    def __post_init__(self) -> None:
        if self.in_channels < 1:
            raise ValueError("in_channels должен быть положительным")
        if self.n_stages < 1:
            raise ValueError("n_stages должен быть положительным")
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError("dropout должен быть в диапазоне [0, 1)")


def build_model(config: SiameseConfig):
    """Собрать сеть. PyTorch импортируется здесь, а не на уровне модуля."""
    import torch.nn as nn

    return SiameseChangeNet(config, nn)


class _LazyTorchMixin:
    """Хранит ссылку на torch.nn, полученную при сборке модели."""


def _residual_block(nn, in_ch: int, out_ch: int, stride: int):
    """Остаточный блок: две свёртки 3x3 плюс обходное соединение.

    Обходное соединение нужно потому, что градиент через четыре стадии
    затухает; с ним сеть обучается устойчиво даже на нескольких сотнях
    примеров. Это ровно та конструкция, что в ResNet, но записанная явно.
    """
    import torch

    class ResidualBlock(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.conv1 = nn.Conv2d(in_ch, out_ch, kernel_size=3, stride=stride, padding=1, bias=False)
            self.norm1 = nn.BatchNorm2d(out_ch)
            self.conv2 = nn.Conv2d(out_ch, out_ch, kernel_size=3, stride=1, padding=1, bias=False)
            self.norm2 = nn.BatchNorm2d(out_ch)
            self.act = nn.ReLU(inplace=True)
            # Проекция нужна, только если меняется форма
            self.shortcut = (
                nn.Sequential(
                    nn.Conv2d(in_ch, out_ch, kernel_size=1, stride=stride, bias=False),
                    nn.BatchNorm2d(out_ch),
                )
                if (stride != 1 or in_ch != out_ch)
                else nn.Identity()
            )

        def forward(self, x):
            identity = self.shortcut(x)
            out = self.act(self.norm1(self.conv1(x)))
            out = self.norm2(self.conv2(out))
            return self.act(out + identity)

    return ResidualBlock()


def make_encoder(config: SiameseConfig):
    """Энкодер: (B, C, H, W) -> (B, embedding_dim).

    Стадии удваивают число каналов и вдвое уменьшают разрешение.
    Завершается глобальным усреднением: размер чипа перестаёт влиять
    на размерность выхода, и модель, обученную на 64x64, можно применять
    к чипам другого размера без переобучения.
    """
    import torch.nn as nn

    layers: list = [
        nn.Conv2d(config.in_channels, config.stem_channels, kernel_size=3, padding=1, bias=False),
        nn.BatchNorm2d(config.stem_channels),
        nn.ReLU(inplace=True),
    ]

    channels = config.stem_channels
    for stage in range(config.n_stages):
        out_channels = channels if stage == 0 else channels * 2
        stride = 1 if stage == 0 else 2
        layers.append(_residual_block(nn, channels, out_channels, stride))
        channels = out_channels

    layers += [
        nn.AdaptiveAvgPool2d(1),
        nn.Flatten(),
        nn.Linear(channels, config.embedding_dim),
        nn.ReLU(inplace=True),
    ]
    return nn.Sequential(*layers)


def make_head(config: SiameseConfig):
    """Голова классификатора поверх пары представлений.

    Вход — конкатенация четырёх векторов:
        |f_after − f_before|  насколько сильно изменилось
        f_after * f_before    что осталось общим
        f_before              из какого состояния
        f_after               в какое состояние
    """
    import torch.nn as nn

    d = config.embedding_dim
    return nn.Sequential(
        nn.Linear(4 * d, d),
        nn.ReLU(inplace=True),
        nn.Dropout(config.dropout),
        nn.Linear(d, d // 2),
        nn.ReLU(inplace=True),
        nn.Dropout(config.dropout),
        nn.Linear(d // 2, 1),
    )


def SiameseChangeNet(config: SiameseConfig, nn):
    """Собрать модуль сети (класс определяется внутри из-за ленивого импорта)."""
    import torch

    class _SiameseChangeNet(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.config = config
            self.encoder = make_encoder(config)
            self.head = make_head(config)

        def embed(self, x):
            """Представление одной эпохи. Веса общие для обеих эпох."""
            return self.encoder(x)

        def forward(self, before, after):
            """Логит принадлежности к классу «свалка».

            Возвращается именно логит, а не вероятность: функция потерь
            BCEWithLogitsLoss численно устойчивее, чем sigmoid + BCE.
            """
            f_before = self.embed(before)
            f_after = self.embed(after)
            features = torch.cat(
                [
                    torch.abs(f_after - f_before),
                    f_after * f_before,
                    f_before,
                    f_after,
                ],
                dim=1,
            )
            return self.head(features).squeeze(1)

        @torch.no_grad()
        def predict_proba(self, before, after):
            """Вероятность класса «свалка»."""
            self.eval()
            return torch.sigmoid(self.forward(before, after))

        def n_parameters(self) -> int:
            return sum(p.numel() for p in self.parameters() if p.requires_grad)

    return _SiameseChangeNet()


__all__ = ["SiameseChangeNet", "SiameseConfig", "build_model", "make_encoder", "make_head"]
