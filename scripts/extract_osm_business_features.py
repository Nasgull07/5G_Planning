
import osmium
import csv
from pathlib import Path
from shapely.geometry import Polygon, LineString

# ==========================================
# 1. PROJECT PATHS
# ==========================================

PROJECT_DIR = Path(__file__).resolve().parent.parent

OSM_FILE = (
    PROJECT_DIR / "data" / "input" / "osm"
    / "cataluna-latest.osm.pbf"
)

OUTPUT_FILE = (
    PROJECT_DIR / "data" / "processed"
    / "osm_business_features.csv"
)

# ==========================================
# 2. BUSINESS FEATURE HANDLER
# ==========================================

class BusinessHandler(osmium.SimpleHandler):

    def __init__(self):
        super().__init__()
        self.features = []
        self.seen = set()

    def save_feature(self, obj, point, geometry_type):

        key = (obj.type_str(), obj.id)

        if key in self.seen:
            return

        self.seen.add(key)

        tags = dict(obj.tags)

        office = tags.get("office", "")
        building = tags.get("building", "")
        landuse = tags.get("landuse", "")
        shop = tags.get("shop", "")

        if office:
            category = "office"
        elif building == "office":
            category = "office"
        elif landuse == "commercial":
            category = "commercial"
        elif landuse == "retail":
            category = "retail"
        elif shop:
            category = "shop"
        else:
            return

        self.features.append({
            "osm_type": obj.type_str(),
            "osm_id": obj.id,
            "category": category,
            "latitude": point.y,
            "longitude": point.x,
            "geometry_type": geometry_type,
            "office": office,
            "building": building,
            "landuse": landuse,
            "shop": shop
        })

    # ======================================
    # 3. OSM NODES
    # ======================================

    def node(self, n):

        if not n.location.valid():
            return

        if not (
            "office" in n.tags
            or "shop" in n.tags
        ):
            return

        from shapely.geometry import Point

        point = Point(
            n.location.lon,
            n.location.lat
        )

        self.save_feature(
            n,
            point,
            "node"
        )

    # ======================================
    # 4. OSM WAYS
    # ======================================

    def way(self, w):

        tags = w.tags

        if not (
            "office" in tags
            or tags.get("building") == "office"
            or tags.get("landuse") in ["commercial", "retail"]
            or "shop" in tags
        ):
            return

        coordinates = []

        for node in w.nodes:

            if not node.location.valid():
                return

            coordinates.append((
                node.location.lon,
                node.location.lat
            ))

        if len(coordinates) < 2:
            return

        try:

            # Closed ways are treated as polygons.
            if (
                len(coordinates) >= 4
                and coordinates[0] == coordinates[-1]
            ):

                polygon = Polygon(coordinates)

                if not polygon.is_valid:
                    polygon = polygon.buffer(0)

                if polygon.is_empty:
                    return

                point = polygon.representative_point()
                geometry_type = "polygon"

            else:

                # Open ways use their midpoint.
                line = LineString(coordinates)

                point = line.interpolate(
                    0.5,
                    normalized=True
                )

                geometry_type = "line"

            self.save_feature(
                w,
                point,
                geometry_type
            )

        except Exception:
            return


# ==========================================
# 5. MAIN EXTRACTION
# ==========================================

def main():

    print("======================================")
    print("IMPROVED OSM BUSINESS EXTRACTION")
    print("======================================")

    if not OSM_FILE.exists():
        raise FileNotFoundError(OSM_FILE)

    print("Reading Catalonia OSM PBF file...")
    print("Calculating representative feature points.")

    handler = BusinessHandler()

    handler.apply_file(
        str(OSM_FILE),
        locations=True
    )

    print(
        f"Unique features extracted: {len(handler.features)}"
    )

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    fieldnames = [
        "osm_type",
        "osm_id",
        "category",
        "latitude",
        "longitude",
        "geometry_type",
        "office",
        "building",
        "landuse",
        "shop"
    ]

    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as csvfile:

        writer = csv.DictWriter(
            csvfile,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(handler.features)

    print("--------------------------------------")
    print("IMPROVED EXTRACTION COMPLETED")
    print("--------------------------------------")

    print("Output:", OUTPUT_FILE)

    print("\nGeometry types:")

    for geometry_type in ["node", "line", "polygon"]:

        count = sum(
            f["geometry_type"] == geometry_type
            for f in handler.features
        )

        print(f"{geometry_type}: {count}")

    print("\nCategory counts:")

    counts = {}

    for feature in handler.features:

        category = feature["category"]

        counts[category] = counts.get(category, 0) + 1

    for category, count in sorted(counts.items()):
        print(f"{category}: {count}")


if __name__ == "__main__":
    main()
