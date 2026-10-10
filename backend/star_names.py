"""Small lookup of bright named stars (J2000 RA/Dec in degrees).
tetra3 returns only coordinates (and catalog IDs), not star names, so we match
solved star positions against this table to attach proper names.
"""
import numpy as np

NAMED_STARS = {
    "Sirius": (101.287, -16.716), "Canopus": (95.988, -52.696),
    "Arcturus": (213.915, 19.182), "Rigil Kentaurus": (219.900, -60.834),
    "Vega": (279.234, 38.784), "Capella": (79.172, 45.998),
    "Rigel": (78.634, -8.202), "Procyon": (114.825, 5.225),
    "Betelgeuse": (88.793, 7.407), "Achernar": (24.429, -57.237),
    "Hadar": (210.956, -60.373), "Altair": (297.696, 8.868),
    "Acrux": (186.650, -63.099), "Aldebaran": (68.980, 16.509),
    "Antares": (247.352, -26.432), "Spica": (201.298, -11.161),
    "Pollux": (116.329, 28.026), "Fomalhaut": (344.413, -29.622),
    "Deneb": (310.358, 45.280), "Mimosa": (191.930, -59.689),
    "Regulus": (152.093, 11.967), "Adhara": (104.656, -28.972),
    "Castor": (113.650, 31.888), "Gacrux": (187.791, -57.113),
    "Shaula": (263.402, -37.104), "Bellatrix": (81.283, 6.350),
    "Elnath": (81.573, 28.608), "Miaplacidus": (138.300, -69.717),
    "Alnilam": (84.053, -1.202), "Alnair": (332.058, -46.961),
    "Alnitak": (85.190, -1.943), "Alioth": (193.507, 55.960),
    "Dubhe": (165.932, 61.751), "Mirfak": (51.081, 49.861),
    "Wezen": (107.098, -26.393), "Sargas": (264.330, -42.998),
    "Kaus Australis": (276.043, -34.385), "Avior": (125.629, -59.510),
    "Alkaid": (206.886, 49.313), "Menkalinan": (89.882, 44.947),
    "Atria": (252.166, -69.028), "Polaris": (37.955, 89.264),
    "Mintaka": (83.002, -0.299), "Saiph": (86.939, -9.670),
    "Alphard": (141.897, -8.659), "Hamal": (31.793, 23.462),
    "Algol": (47.042, 40.956), "Denebola": (177.265, 14.572),
    "Mizar": (200.981, 54.925), "Schedar": (10.127, 56.537),
    "Alpheratz": (2.097, 29.091), "Mirach": (17.433, 35.621),
    "Enif": (326.046, 9.875), "Markab": (346.190, 15.205),
    "Rasalhague": (263.734, 12.560), "Albireo": (292.680, 27.960),
    "Sadr": (305.557, 40.257), "Thuban": (211.097, 64.376),
    "Merak": (165.460, 56.382), "Phecda": (178.457, 53.695),
    "Megrez": (183.857, 57.033), "Kochab": (222.676, 74.156),
    "Nunki": (283.816, -26.297), "Zubenelgenubi": (222.720, -16.042),
    "Algieba": (154.993, 19.842), "Eltanin": (269.151, 51.489),
    "Unukalhai": (236.067, 6.426), "Alphecca": (233.672, 26.715),
    "Diphda": (10.897, -17.987), "Navi": (14.177, 60.717),
    "Ruchbah": (21.454, 60.235), "Alcyone": (56.871, 24.105),
    "Sheratan": (28.660, 20.808),
}

_names = list(NAMED_STARS.keys())
_ra = np.radians([v[0] for v in NAMED_STARS.values()])
_dec = np.radians([v[1] for v in NAMED_STARS.values()])


def name_star(ra_deg: float, dec_deg: float, tol_deg: float = 0.15):
    """Return the proper name of a star near (ra, dec), or None."""
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    cos_sep = np.sin(dec) * np.sin(_dec) + np.cos(dec) * np.cos(_dec) * np.cos(ra - _ra)
    sep = np.degrees(np.arccos(np.clip(cos_sep, -1, 1)))
    i = int(np.argmin(sep))
    return _names[i] if sep[i] <= tol_deg else None
