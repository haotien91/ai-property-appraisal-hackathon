from getFacility import (
    FuneralFacilityCategory,
    find_nearest_funeral_facilities,
)

# 使用者提供格式為「緯度, 經度」：25.222398, 121.637880。
# 函式參數則是 longitude 在前、latitude 在後，不能對調。
longitude = 121.637880
latitude = 25.222398
radius_meters = 2000

print(
    f"查詢中心：緯度 {latitude}, 經度 {longitude}；"
    f"最大半徑：{radius_meters} 公尺"
)

nearest = find_nearest_funeral_facilities(
    longitude=longitude,
    latitude=latitude,
    radius_meters=radius_meters,
)

print("\n=== 各類最近殯葬設施 ===")
for category in FuneralFacilityCategory:
    facility = nearest[category]
    if facility is None:
        print(f"{category.label}：{radius_meters}m 內查無")
        continue

    if facility.mark_type != category.mark_type:
        raise AssertionError(
            f"{category.label}分類錯誤：預期 {category.mark_type}，"
            f"實際 {facility.mark_type}"
        )
    if facility.distance_meters > radius_meters:
        raise AssertionError(
            f"{facility.name} 距離超出查詢半徑："
            f"{facility.distance_meters}m"
        )

    print(
        f"{category.label}：{facility.name}；"
        f"距離 {facility.distance_meters}m；"
        f"marktype={facility.mark_type}；"
        f"坐標=({facility.longitude}, {facility.latitude})"
    )

cemetery = nearest[FuneralFacilityCategory.CEMETERY]
columbarium = nearest[FuneralFacilityCategory.COLUMBARIUM]
if cemetery is None or "第一公墓" not in cemetery.name:
    raise AssertionError("2000m 內的最近墓地不是預期的金山區第一公墓")
if columbarium is None or "福緣納骨堂" not in columbarium.name:
    raise AssertionError("2000m 內的最近納骨塔不是預期的福緣納骨堂")

print(
    "\n注意：COM_012 的 distance 是查詢點到設施代表點的距離，"
    "不是查詢點到公墓用地邊界的最短距離。"
)
