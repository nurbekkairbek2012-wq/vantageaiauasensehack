"""Версии геостека — первое, что нужно знать при расхождении машин.

Тесты геометрии проходят локально и падают на CI; разница почти всегда в
версии PROJ, GEOS или GDAL, а не в коде. Печатать их дешевле, чем гадать.
"""

import sys

print("python", sys.version.split()[0], sys.platform)
for name in ("numpy", "geopandas", "shapely", "pyproj", "rasterio", "pandas", "scipy"):
    try:
        module = __import__(name)
        print(name, getattr(module, "__version__", "?"))
    except ImportError:
        print(name, "НЕ УСТАНОВЛЕН")

try:
    import pyproj
    print("PROJ", pyproj.proj_version_str)
except Exception:
    pass
try:
    import rasterio
    print("GDAL", rasterio.__gdal_version__)
except Exception:
    pass
try:
    import shapely
    print("GEOS", shapely.geos_version_string)
except Exception:
    pass
