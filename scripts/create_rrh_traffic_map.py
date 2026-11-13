
import csv
import json
from pathlib import Path
import xml.etree.ElementTree as ET

# =====================================================
# 1. PROJECT PATHS
# =====================================================

PROJECT_DIR = Path(__file__).resolve().parent.parent

DATA_FILE = (
    PROJECT_DIR
    / "data"
    / "processed"
    / "scenario_sites_classified.csv"
)

KML_FILE = (
    PROJECT_DIR
    / "data"
    / "input"
    / "working_area_900km2.kml"
)

OUTPUT_FILE = (
    PROJECT_DIR
    / "output"
    / "rrh_traffic_distribution_map.html"
)

# =====================================================
# 2. TRAFFIC CATEGORY COLOURS
# =====================================================

COLORS = {
    "Residential": "#F59E0B",
    "Office": "#3B82F6",
    "Mixed": "#16A34A"
}


# =====================================================
# 3. READ CLASSIFIED RRH DATASET
# =====================================================

def read_rrhs():

    if not DATA_FILE.exists():
        raise FileNotFoundError(
            f"Dataset not found: {DATA_FILE}"
        )

    sites = []

    with DATA_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(file)

        required_columns = {
            "rrh_id",
            "latitude",
            "longitude",
            "technology",
            "is_synthetic",
            "traffic_type"
        }

        if not reader.fieldnames or not required_columns.issubset(
            reader.fieldnames
        ):
            raise ValueError(
                "The classified CSV is missing required columns."
            )

        for row in reader:

            traffic = row["traffic_type"].strip()

            if traffic not in COLORS:
                raise ValueError(
                    f"Unknown traffic category: {traffic}"
                )

            sites.append({
                "rrh_id": row["rrh_id"],
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "technology": row["technology"],
                "traffic_type": traffic,
                "is_synthetic": (
                    row["is_synthetic"].strip().lower() == "true"
                )
            })

    return sites


# =====================================================
# 4. READ KML STUDY AREA BOUNDARY
# =====================================================

def read_kml():

    if not KML_FILE.exists():

        print("WARNING: KML boundary not found.")

        return None

    root = ET.parse(KML_FILE).getroot()

    polygons = []

    for polygon in root.findall(".//{*}Polygon"):

        outer_node = polygon.find(
            "./{*}outerBoundaryIs/{*}LinearRing/{*}coordinates"
        )

        if outer_node is None or not outer_node.text:
            continue

        def parse_ring(node):

            points = []

            for item in node.text.split():

                values = item.split(",")

                if len(values) >= 2:

                    longitude = float(values[0])
                    latitude = float(values[1])

                    points.append([
                        longitude,
                        latitude
                    ])

            return points

        outer = parse_ring(outer_node)

        if len(outer) < 4:
            continue

        rings = [outer]

        # Read inner boundaries if present
        for inner_node in polygon.findall(
            "./{*}innerBoundaryIs/{*}LinearRing/{*}coordinates"
        ):

            if inner_node.text:

                inner = parse_ring(inner_node)

                if len(inner) >= 4:
                    rings.append(inner)

        polygons.append(rings)

    if not polygons:
        return None

    if len(polygons) == 1:

        return {
            "type": "Polygon",
            "coordinates": polygons[0]
        }

    return {
        "type": "MultiPolygon",
        "coordinates": polygons
    }


# =====================================================
# 5. CREATE INTERACTIVE HTML MAP
# =====================================================

def create_map(sites, boundary):

    site_data = json.dumps(
        sites,
        ensure_ascii=False
    ).replace("</", "<\\/")

    boundary_data = (
        json.dumps(boundary)
        if boundary
        else "null"
    )

    center_lat = sum(
        site["latitude"] for site in sites
    ) / len(sites)

    center_lon = sum(
        site["longitude"] for site in sites
    ) / len(sites)

    page = f"""<!DOCTYPE html>

<html>

<head>

<meta charset="utf-8">

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Barcelona RRH Traffic Distribution Map</title>

<link rel="stylesheet"
href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">

<style>

html, body, #map {{

    height: 100%;
    width: 100%;
    margin: 0;
    padding: 0;

}}

.legend {{

    background: white;
    padding: 12px;
    line-height: 1.8;
    border-radius: 5px;
    box-shadow: 0 1px 5px #777;
    font-family: Arial, sans-serif;
    font-size: 13px;

}}

.legend-symbol {{

    display: inline-block;
    width: 13px;
    height: 13px;
    margin-right: 8px;
    vertical-align: middle;
    border: 1px solid #222;
    box-sizing: border-box;

}}

.circle {{

    border-radius: 50%;
    background: #F59E0B;

}}

.square {{

    background: #3B82F6;

}}

.triangle {{

    width: 0;
    height: 0;
    border-left: 7px solid transparent;
    border-right: 7px solid transparent;
    border-bottom: 14px solid #16A34A;
    border-top: none;
    background: transparent;

}}

.source-symbol {{

    display: inline-block;
    width: 13px;
    height: 13px;
    border: 1px solid #222;
    border-radius: 50%;
    margin-right: 8px;
    vertical-align: middle;

}}

.synthetic-source {{

    border: 3px solid #222;

}}

</style>

</head>

<body>

<div id="map"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js">
</script>

<script>

// =================================================
// 6. DATA
// =================================================

const sites = {site_data};

const boundary = {boundary_data};

const colors = {{

    "Residential": "#F59E0B",
    "Office": "#3B82F6",
    "Mixed": "#16A34A"

}};


// =================================================
// 7. INITIALIZE MAP
// =================================================

const map = L.map("map").setView(
    [{center_lat}, {center_lon}],
    11
);


// =================================================
// 8. BASE MAP
// =================================================

const basemap = L.tileLayer(

    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{{z}}/{{y}}/{{x}}",

    {{

        maxZoom: 19,

        attribution: "Tiles © Esri and its contributors"

    }}

).addTo(map);


// =================================================
// 9. CREATE TRAFFIC CATEGORY LAYERS
// =================================================

const layers = {{

    "Residential": L.layerGroup(),

    "Office": L.layerGroup(),

    "Mixed": L.layerGroup()

}};

const bounds = [];


// =================================================
// 10. ADD RRH MARKERS
// =================================================

sites.forEach(site => {{

    const color = colors[site.traffic_type];

    const borderWidth = site.is_synthetic ? 3 : 1;

    const markerSize = site.is_synthetic ? 15 : 12;

    let marker;


    // ---------------------------------------------
    // RESIDENTIAL = CIRCLE
    // ---------------------------------------------

    if (site.traffic_type === "Residential") {{

        marker = L.circleMarker(

            [site.latitude, site.longitude],

            {{

                radius: site.is_synthetic ? 7 : 5,

                color: "#222222",

                weight: borderWidth,

                fillColor: color,

                fillOpacity: 0.85

            }}

        );

    }}


    // ---------------------------------------------
    // OFFICE = SQUARE
    // ---------------------------------------------

    else if (site.traffic_type === "Office") {{

        marker = L.marker(

            [site.latitude, site.longitude],

            {{

                icon: L.divIcon({{

                    className: "",

                    html: `<div style="
                        width: ${{markerSize}}px;
                        height: ${{markerSize}}px;
                        background: ${{color}};
                        border: ${{borderWidth}}px solid #222222;
                        box-sizing: border-box;
                    "></div>`,

                    iconSize: [markerSize + 2, markerSize + 2],

                    iconAnchor: [
                        (markerSize + 2) / 2,
                        (markerSize + 2) / 2
                    ]

                }})

            }}

        );

    }}


    // ---------------------------------------------
    // MIXED = TRIANGLE
    // ---------------------------------------------

    else if (site.traffic_type === "Mixed") {{

        marker = L.marker(

            [site.latitude, site.longitude],

            {{

                icon: L.divIcon({{

                    className: "",

                    html: `<div style="
                        width: 0;
                        height: 0;
                        border-left: 8px solid transparent;
                        border-right: 8px solid transparent;
                        border-bottom: 15px solid ${{color}};
                        filter: drop-shadow(0 0 1px #222222);
                    "></div>`,

                    iconSize: [16, 16],

                    iconAnchor: [8, 8]

                }})

            }}

        );

    }}


    // ---------------------------------------------
    // POPUP INFORMATION
    // ---------------------------------------------

    const source = site.is_synthetic
        ? "Synthetic RRH (simulated)"
        : "Real 4G/5G location";

    const popup = `

        <b>RRH ID:</b> ${{site.rrh_id}}<br>

        <b>Traffic category:</b> ${{site.traffic_type}}<br>

        <b>Source:</b> ${{source}}<br>

        <b>Technology:</b> ${{site.technology}}<br>

        <b>Latitude:</b> ${{site.latitude.toFixed(6)}}<br>

        <b>Longitude:</b> ${{site.longitude.toFixed(6)}}

    `;

    marker.bindPopup(popup);

    marker.bindTooltip(site.traffic_type);

    marker.addTo(layers[site.traffic_type]);

    bounds.push([
        site.latitude,
        site.longitude
    ]);

}});


// =================================================
// 11. ADD LAYERS TO MAP
// =================================================

Object.values(layers).forEach(layer => {{

    layer.addTo(map);

}});


// =================================================
// 12. LAYER CONTROL
// =================================================

const overlayLayers = {{

    "Residential - Circles": layers["Residential"],

    "Office - Squares": layers["Office"],

    "Mixed - Triangles": layers["Mixed"]

}};

const boundaryLayer = boundary
    ? L.geoJSON(boundary, {{

        style: {{

            color: "#DC2626",

            weight: 3,

            fillOpacity: 0.04

        }}

    }})
    : null;

const controls = {{

    "Esri Street Map": basemap

}};

if (boundaryLayer) {{

    overlayLayers["Study Area Boundary"] = boundaryLayer;

}}

L.control.layers(

    controls,

    overlayLayers,

    {{

        collapsed: false

    }}

).addTo(map);


// =================================================
// 13. MAP LEGEND
// =================================================

const legend = L.control({{

    position: "bottomright"

}});

legend.onAdd = function() {{

    const div = L.DomUtil.create(
        "div",
        "legend"
    );

    div.innerHTML = `

        <b>RRH Traffic Categories</b><br>

        <span class="legend-symbol circle"></span>
        Residential - Circle<br>

        <span class="legend-symbol square"></span>
        Office - Square<br>

        <span class="legend-symbol triangle"></span>
        Mixed - Triangle<br>

        <hr>

        <b>RRH Source</b><br>

        <span class="source-symbol"></span>
        Real RRH - Thin outline<br>

        <span class="source-symbol synthetic-source"></span>
        Synthetic RRH - Thick outline

    `;

    return div;

}};

legend.addTo(map);


// =================================================
// 14. ZOOM TO RRH LOCATIONS
// =================================================

if (bounds.length > 0) {{

    map.fitBounds(
        bounds,
        {{

            padding: [20, 20]

        }}
    );

}}


// =================================================
// END OF MAP
// =================================================

</script>

</body>

</html>
"""

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    OUTPUT_FILE.write_text(
        page,
        encoding="utf-8"
    )


# =====================================================
# 15. MAIN PROGRAM
# =====================================================

def main():

    print("Reading classified RRH dataset...")

    sites = read_rrhs()

    print("Reading KML study area...")

    boundary = read_kml()

    print("Creating interactive traffic map...")

    create_map(sites, boundary)

    print("\n===================================")

    print("TRAFFIC DISTRIBUTION MAP COMPLETED")

    print("===================================")

    print(f"Total RRHs: {len(sites)}")

    for category in COLORS:

        count = sum(
            site["traffic_type"] == category
            for site in sites
        )

        print(f"{category}: {count}")

    print("\nMap saved to:")

    print(OUTPUT_FILE)


if __name__ == "__main__":

    main()
