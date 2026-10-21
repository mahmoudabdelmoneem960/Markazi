# أدوات التجهيز (مرة واحدة)

## 1) قاعدة الأماكن `data/gazetteer.sqlite` (لتحويل العناوين لإحداثيات، من غير إنترنت)
المصادر: خريطة مصر من OpenStreetMap + أماكن GeoNames. البناء محتاج مكتبة `osmium` (متاحة لـ Python 3.12).
```
py -3.12 -m venv .venv-gaz
.venv-gaz\Scripts\pip install osmium
```
1. نزّل خريطة مصر (~180 ميجا): https://download.geofabrik.de/africa/egypt-latest.osm.pbf
2. نزّل GeoNames لمصر: https://download.geonames.org/export/dump/EG.zip وفُك `EG.txt`
3. ابنِ القاعدة (حوالي 5 دقايق):
```
.venv-gaz\Scripts\python tools\build_gazetteer.py egypt-latest.osm.pbf data\gazetteer.sqlite
.venv-gaz\Scripts\python tools\add_geonames.py data\gazetteer.sqlite EG.txt
```
الترخيص: بيانات OSM برخصة ODbL (لازم ذكر المصدر ومشاركة أي اشتقاق بنفس الرخصة)، وGeoNames برخصة CC BY 4.0.

## 2) سيرفر OSRM (مسافات الطرق الفعلية)
`tools/osrm/start_osrm.bat` بيشغّل السيرفر على `127.0.0.1:5001` من فولدر `%USERPROFILE%\osrm` (أو المتغير `OSRM_HOME`)، و`stop_osrm.bat` بيوقفه. التجهيز الكامل (أدوات OSRM + خريطة مصر + extract/partition/customize) مشروح في README الرئيسي قسم "OSRM على جهازك".

## 3) شيتات تجريبية
`python tools\make_sample.py` بيولّد `sample/students_sample.xlsx` و`sample/centers_sample.csv` ببيانات وهمية بالكامل (أرقام قومية عشوائية) من أسماء أماكن حقيقية في قاعدة الأماكن.
