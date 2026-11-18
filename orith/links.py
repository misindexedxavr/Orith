"""Links for the analyst to open BY HAND. Nothing here opens a browser, uploads or fetches anything.

The URL patterns are the providers' public link formats. Coordinates are WGS84; where a provider
uses another datum (Baidu uses BD-09), the link passes coord_type=wgs84 so the provider converts.
"""
from __future__ import annotations

import urllib.parse


def place_links(lat, lon, zoom=18):
    la, lo = f"{lat:.6f}", f"{lon:.6f}"
    q = urllib.parse.quote
    return {
        "maps": {
            "Google Maps (satellite)": f"https://www.google.com/maps/@?api=1&map_action=map&center={la},{lo}&zoom={zoom}&basemap=satellite",
            "OpenStreetMap": f"https://www.openstreetmap.org/?mlat={la}&mlon={lo}#map={zoom}/{la}/{lo}",
            "Bing Maps (aerial)": f"https://www.bing.com/maps?cp={la}~{lo}&lvl={zoom}&style=a",
            "Yandex Maps (satellite)": f"https://yandex.com/maps/?ll={lo},{la}&z={zoom}&l=sat",
            "Apple Maps (satellite)": f"https://maps.apple.com/?ll={la},{lo}&z={zoom}&t=k",
            "Baidu Maps (WGS84 marker)": f"https://api.map.baidu.com/marker?location={la},{lo}&coord_type=wgs84&title={q('point')}&content={q('point')}&output=html",
            "Wikimapia": f"https://wikimapia.org/#lat={la}&lon={lo}&z={zoom}",
        },
        "street_level": {
            "Google Street View": f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={la},{lo}",
            "Mapillary": f"https://www.mapillary.com/app/?lat={la}&lng={lo}&z={zoom}",
            "Yandex panoramas": f"https://yandex.com/maps/?ll={lo},{la}&z={zoom}&panorama%5Bpoint%5D={lo},{la}",
        },
        "imagery_and_terrain": {
            "Google Earth (web)": f"https://earth.google.com/web/@{la},{lo},0a,1000d,35y,0h,0t,0r",
            "Esri Wayback (historical imagery)":
                f"https://livingatlas.arcgis.com/wayback/#mapCenter={lo}%2C{la}%2C{min(zoom, 19)}",
            "Sentinel Hub EO Browser": f"https://apps.sentinel-hub.com/eo-browser/?lat={la}&lng={lo}&zoom={min(zoom, 16)}",
        },
        "tools": {
            "Overpass turbo (map centred here)": f"https://overpass-turbo.eu/?C={la};{lo};{min(zoom, 17)}",
        },
    }


def sun_link(lat, lon, utc_iso=None, zoom=17):
    """SunCalc page for a place and (optionally) a date/time; the time shown there is local."""
    base = f"https://www.suncalc.org/#/{lat:.5f},{lon:.5f},{zoom}"
    if utc_iso:
        d, t = utc_iso[:10].replace("-", "."), utc_iso[11:16]
        return base + f"/{d}/{t}/1/3"
    return base


REVERSE_IMAGE_PAGES = {
    "Google Lens (upload by hand)": "https://lens.google.com/",
    "Bing Visual Search (upload by hand)": "https://www.bing.com/visualsearch",
    "Yandex Images (upload by hand)": "https://yandex.com/images/",
    "TinEye (upload by hand)": "https://tineye.com/",
}

NOTE = ("Printed only: open each link yourself. The toolkit never opens a browser, uploads an image or "
        "runs a search. URL formats are the providers' public link formats and can change; if one does not "
        "centre on the point, use the coordinates printed above.")


def all_links(lat=None, lon=None, zoom=18, utc_iso=None):
    out = {"note": NOTE, "reverse_image_search": REVERSE_IMAGE_PAGES}
    if lat is not None and lon is not None:
        out["point"] = [round(lat, 7), round(lon, 7)]
        out.update(place_links(lat, lon, zoom))
        out["tools"]["SunCalc (sun and shadows here)"] = sun_link(lat, lon, utc_iso)
    return out
