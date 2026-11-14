#!/usr/bin/env python3
"""Render an existing delay analysis, selecting its CSV inputs by K and S."""

import argparse
import colorsys
import csv
import html
import math
from pathlib import Path

import folium


ROOT = Path(__file__).resolve().parent.parent


def input_paths(k, s, root=ROOT):
    if k < 1 or s < 0:
        raise ValueError("K must be positive and S must be nonnegative")
    tag = f"k{k}" + (f"_s{s}" if s else "")
    directory = root / "output" / "delay"
    paths = {
        "summary": directory / f"rrh_delay_{tag}_summary.csv",
        "links": directory / f"rrh_pool_{tag}_links.csv",
        "pools": root / "data" / "processed" / f"bbu_pool_{tag}_kmedian_locations.csv",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing input CSV(s):\n" + "\n".join(missing))
    return paths, directory / f"rrh_delay_{tag}_map.html"


def read_csv(path, required):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name}: missing columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path.name}: empty dataset")
    return rows


def flag(value):
    value = str(value).strip().lower()
    if value not in {"true", "false", "1", "0", "t", "f"}:
        raise ValueError(f"Invalid boolean: {value!r}")
    return value in {"true", "1", "t"}


def coordinates(row):
    lat, lon = float(row["latitude"]), float(row["longitude"])
    if not (math.isfinite(lat) and math.isfinite(lon)
            and -90 < lat < 90 and -180 <= lon <= 180):
        raise ValueError("Invalid geographic coordinates")
    return [lat, lon]


def indexed(rows, key):
    result = {}
    for row in rows:
        identifier = row[key]
        if not identifier.strip() or identifier in result:
            raise ValueError(f"Empty or duplicate {key}: {identifier!r}")
        coordinates(row)
        result[identifier] = row
    return result


def load_inputs(paths, k, s):
    rrhs = indexed(read_csv(paths["summary"], {
        "rrh_id", "latitude", "longitude", "is_synthetic", "original_bbu_pool",
        "nearest_bbu_pool", "nearest_distance_km", "nearest_rtt_us",
        "best_feasible_bbu_pool", "feasible_pool_count", "feasible_pools", "service_status",
    }), "rrh_id")
    pools = indexed(read_csv(paths["pools"], {
        "bbu_pool", "latitude", "longitude",
    }), "bbu_pool")
    if len(pools) != k or sum(flag(row["is_synthetic"]) for row in rrhs.values()) != s:
        raise ValueError("K/S do not match the contents of the input CSVs")
    rows = read_csv(paths["links"], {
        "rrh_id", "bbu_pool", "distance_km", "rtt_us", "is_feasible", "is_best_feasible",
    })
    links = {}
    for row in rows:
        pair = row["rrh_id"], row["bbu_pool"]
        if pair[0] not in rrhs or pair[1] not in pools or pair in links:
            raise ValueError(f"Unknown or duplicate RRH/pool pair: {pair}")
        for field in ("distance_km", "rtt_us"):
            value = float(row[field])
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid {field} for {pair}")
        flag(row["is_feasible"])
        flag(row["is_best_feasible"])
        links[pair] = row
    if len(links) != len(rrhs) * len(pools):
        raise ValueError("Incomplete RRH/pool link matrix")
    for identifier, rrh in rrhs.items():
        feasible = {pool for pool in pools if flag(links[identifier, pool]["is_feasible"])}
        declared = [pool for pool in rrh["feasible_pools"].split(";") if pool]
        best = rrh["best_feasible_bbu_pool"]
        selected = {pool for pool in pools if flag(links[identifier, pool]["is_best_feasible"])}
        nearest = rrh["nearest_bbu_pool"]
        if nearest not in pools or (rrh["original_bbu_pool"] and rrh["original_bbu_pool"] not in pools):
            raise ValueError(f"Unknown pool in summary for {identifier}")
        if (int(rrh["feasible_pool_count"]) != len(feasible)
                or set(declared) != feasible or len(declared) != len(feasible)
                or rrh["service_status"] != ("served" if feasible else "unserved")
                or (feasible and best not in feasible)
                or (not feasible and best)
                or selected != ({best} if best else set())):
            raise ValueError(f"Summary and links disagree for {identifier}")
        nearest_link = links[identifier, nearest]
        for summary_field, link_field in (("nearest_rtt_us", "rtt_us"),
                                          ("nearest_distance_km", "distance_km")):
            if not math.isclose(float(rrh[summary_field]), float(nearest_link[link_field])):
                raise ValueError(f"Nearest link and summary disagree for {identifier}")
    return rrhs, pools, links


def pool_colors(pools):
    colors = {}
    for i, pool in enumerate(sorted(pools)):
        rgb = colorsys.hsv_to_rgb((i * 0.61803398875) % 1, .75, .68)
        colors[pool] = "#" + "".join(f"{round(component * 255):02x}" for component in rgb)
    return colors


def popup(fields):
    return "<br>".join(f"<b>{html.escape(str(label))}:</b> {html.escape(str(value))}"
                         for label, value in fields)


def create_map(rrhs, pools, links, output):
    colors = pool_colors(pools)
    points = [coordinates(row) for row in [*rrhs.values(), *pools.values()]]
    m = folium.Map(location=points[0], zoom_start=11, tiles=None)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Esri Street Map", overlay=False, control=True,
    ).add_to(m)
    pool_layer = folium.FeatureGroup(name="BBU Pools")
    served_layer = folium.FeatureGroup(name="Served RRHs")
    unserved_layer = folium.FeatureGroup(name="Unserved RRHs")
    chosen_layer = folium.FeatureGroup(name="Selected connections")
    alternative_layer = folium.FeatureGroup(name="Feasible alternatives", show=False)
    for pool_id, row in pools.items():
        count = sum(rrh["best_feasible_bbu_pool"] == pool_id for rrh in rrhs.values())
        label = html.escape(pool_id)
        icon = (f'<div style="background:{colors[pool_id]};color:white;border:2px solid white;'
                'border-radius:5px;padding:3px 5px;white-space:nowrap;'
                f'box-shadow:0 1px 4px #555"><i class="fa fa-server"></i> {label}</div>')
        folium.Marker(coordinates(row), tooltip=label,
                      icon=folium.DivIcon(html=icon, icon_size=(100, 27), icon_anchor=(50, 13)),
                      popup=folium.Popup(popup([("Pool", pool_id), ("Assigned RRHs", count)]),
                                         max_width=350)).add_to(pool_layer)
    for identifier, rrh in rrhs.items():
        best = rrh["best_feasible_bbu_pool"]
        synthetic = flag(rrh["is_synthetic"])
        nearest = links[identifier, rrh["nearest_bbu_pool"]]
        chosen = links[identifier, best] if best else None
        fields = [("RRH", identifier), ("Origin", "Synthetic" if synthetic else "Real"),
                  ("Status", rrh["service_status"]),
                  ("Original pool", rrh["original_bbu_pool"] or "Not recorded"),
                  ("Best feasible pool", best or "None"),
                  ("Nearest pool", rrh["nearest_bbu_pool"]),
                  ("Nearest distance (km)", f"{float(nearest['distance_km']):.3f}"),
                  ("Nearest RTT (us)", f"{float(nearest['rtt_us']):.3f}"),
                  ("Feasible pools", rrh["feasible_pools"] or "None")]
        if chosen:
            fields.append(("Selected RTT (us)", f"{float(chosen['rtt_us']):.3f}"))
        tooltip = html.escape(f"{identifier}: {best or 'UNSERVED'}")
        options = dict(tooltip=tooltip, popup=folium.Popup(popup(fields), max_width=450))
        if best:
            folium.CircleMarker(coordinates(rrh), radius=5, color=colors[best],
                                weight=2, fill=True, fill_color=colors[best], fill_opacity=.8,
                                dash_array="3,3" if synthetic else None,
                                **options).add_to(served_layer)
        else:
            border = "dashed" if synthetic else "solid"
            icon = ('<div style="color:#c00000;background:white;text-align:center;'
                    f'font-size:20px;line-height:20px;border:2px {border} #c00000;'
                    'border-radius:50%">&#10005;</div>')
            folium.Marker(coordinates(rrh), icon=folium.DivIcon(
                html=icon, icon_size=(24, 24), icon_anchor=(12, 12)),
                **options).add_to(unserved_layer)
        for pool in pools:
            link = links[identifier, pool]
            if not flag(link["is_feasible"]):
                continue
            selected = pool == best
            folium.PolyLine(
                [coordinates(rrh), coordinates(pools[pool])], color=colors[pool],
                weight=1.8 if selected else 1, opacity=.65 if selected else .3,
                dash_array=None if selected else "5,5",
                tooltip=html.escape(f"{identifier} → {pool}: {float(link['rtt_us']):.3f} us"),
            ).add_to(chosen_layer if selected else alternative_layer)
    # Lines underneath RRHs and pool markers.
    for layer in (chosen_layer, alternative_layer, served_layer, unserved_layer, pool_layer):
        layer.add_to(m)
    served = sum(bool(row["best_feasible_bbu_pool"]) for row in rrhs.values())
    synthetic = sum(flag(row["is_synthetic"]) for row in rrhs.values())
    legend = ('<div style="position:fixed;bottom:25px;left:15px;z-index:9999;'
              'background:white;padding:12px;max-height:40vh;overflow:auto;'
              'font-size:12px;border:1px solid #aaa;border-radius:5px">'
              '<b>Delay feasibility</b><br>'
              f'RRHs: {len(rrhs)} (real: {len(rrhs)-synthetic}, synthetic: {synthetic})<br>'
              f'Served: {served} | Unserved: {len(rrhs)-served}<br>'
              f'Pools: {len(pools)}<br>'
              '● Served · <span style="color:#c00000">✕ Unserved</span><br>'
              'Dashed RRH border: synthetic<br>Dashed line: feasible alternative<hr>')
    legend += "<br>".join(f'<span style="color:{colors[p]}">■</span> {html.escape(p)}'
                            for p in sorted(pools)) + "</div>"
    m.get_root().html.add_child(folium.Element(legend))
    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds([[min(p[0] for p in points), min(p[1] for p in points)],
                  [max(p[0] for p in points), max(p[1] for p in points)]])
    m.save(str(output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-k", "--k-pools", type=int, required=True)
    parser.add_argument("-s", "--synthetic-count", type=int, default=0)
    args = parser.parse_args()
    try:
        paths, output = input_paths(args.k_pools, args.synthetic_count)
        rrhs, pools, links = load_inputs(paths, args.k_pools, args.synthetic_count)
        create_map(rrhs, pools, links, output)
    except (OSError, ValueError, csv.Error) as error:
        parser.error(str(error))
    print(f"Map: {output}")
    print(f"RRHs: {len(rrhs)} | Pools: {len(pools)}")


if __name__ == "__main__":
    main()
