"""
ulpin_generator.py
Prototype implementation of the DoLR/NIC published ULPIN/PNIU
coordinate-encoding procedure and reverse decoding.

Standard Reference:
- Standard: Department of Land Resources (DoLR), Ministry of Rural Development & NIC
- Format: 14-character alphanumeric identifier based on WGS84 coordinates & floor level.
- Arithmetic:
  Latitude (6 decimal places):
    Part 1 (lat_1): (integer lat + 90) -> Base 14 (2 chars)
    Part 2 (lat_3): first 3 fractional digits -> Base 32 (2 chars)
    Part 3 (lat_4): last 3 fractional digits -> Base 32 (2 chars)
  Longitude (6 decimal places):
    Part 1 (lon_1): (integer lon + 180) -> Base 19 (2 chars)
    Part 2 (lon_3): first 3 fractional digits -> Base 32 (2 chars)
    Part 3 (lon_4): last 3 fractional digits -> Base 32 (2 chars)
  Floor:
    Part 1 (floor_code): (floor + 578) -> Base 32 (2 chars)
  
  Ambiguity Substitution: 'I' -> 'Y', 'O' -> 'Z'
"""

import math
from typing import Dict, Any, Tuple


BASE_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def encode_base(value: int, base: int, width: int = 2) -> str:
    """Encode a non-negative integer into a custom base representation and left-pad to width."""
    if value < 0:
        raise ValueError("Value must be non-negative")
    if not 2 <= base <= len(BASE_CHARS):
        raise ValueError(f"Unsupported base {base}")

    if value == 0:
        return "0".rjust(width, "0")
    
    result = ""
    while value > 0:
        value, remainder = divmod(value, base)
        result = BASE_CHARS[remainder] + result
    return result.rjust(width, "0")


def decode_base(code: str, base: int) -> int:
    """Decode a base-N string back to an integer."""
    val = 0
    for char in code.upper():
        idx = BASE_CHARS.find(char)
        if idx == -1 or idx >= base:
            raise ValueError(f"Invalid character '{char}' for base {base}")
        val = val * base + idx
    return val


def ulpin_alphabet(code: str) -> str:
    """Apply ECCMA-style ambiguity substitutions (I->Y, O->Z)."""
    return code.replace("I", "Y").replace("O", "Z")


def reverse_ulpin_alphabet(code: str) -> str:
    """Reverse substitution for decoding (Y->I, Z->O where appropriate, or test both)."""
    return code


def generate_prototype_ulpin(
    latitude: float,
    longitude: float,
    floor: int = 0,
) -> str:
    """
    Generate the 14-character prototype ULPIN identifier from WGS84 coordinates and floor level.
    """
    latitude = round(float(latitude), 6)
    longitude = round(float(longitude), 6)

    if not (-90 <= latitude <= 90):
        raise ValueError("Latitude must be between -90 and 90")
    if not (-180 <= longitude <= 180):
        raise ValueError("Longitude must be between -180 and 180")
    if latitude < 0 or longitude < 0:
        raise NotImplementedError(
            "This prototype follows the positive-coordinate worked example."
        )

    lat_int = int(math.floor(latitude))
    lon_int = int(math.floor(longitude))

    lat_fraction = f"{latitude:.6f}".split(".")[1]
    lon_fraction = f"{longitude:.6f}".split(".")[1]

    lat_1 = ulpin_alphabet(encode_base(lat_int + 90, 14, 2))
    lat_3 = ulpin_alphabet(encode_base(int(lat_fraction[:3]), 32, 2))
    lat_4 = ulpin_alphabet(encode_base(int(lat_fraction[3:]), 32, 2))

    lon_1 = ulpin_alphabet(encode_base(lon_int + 180, 19, 2))
    lon_3 = ulpin_alphabet(encode_base(int(lon_fraction[:3]), 32, 2))
    lon_4 = ulpin_alphabet(encode_base(int(lon_fraction[3:]), 32, 2))

    floor_code = ulpin_alphabet(encode_base(floor + 578, 32, 2))

    return lat_1 + lat_3 + lat_4 + lon_1 + lon_3 + lon_4 + floor_code


def generate_3d_ulpin_18char(
    latitude: float,
    longitude: float,
    floor: int = 0,
    unit_code: str = None
) -> str:
    """
    Generate 18-character 3D ULPIN for vertical property units.
    - First 14 characters: Exact 2D parcel base ULPIN (identical across all floors/apartments in the building).
    - Next 4 characters: Vertical/Y-axis level suffix (-F00, -F01, -F04, -B01, -UTL).
    Total length = 18 characters.
    """
    base_14 = generate_prototype_ulpin(latitude, longitude, floor=0)
    
    if unit_code:
        suffix = f"-{unit_code[:3].upper()}".rjust(4, "-")
    elif floor < 0:
        suffix = f"-B{abs(floor):02d}"
    else:
        suffix = f"-F{floor:02d}"

    return base_14 + suffix


def decode_prototype_ulpin(ulpin: str) -> Dict[str, Any]:
    """
    Reverse-decode a 14-character 2D ULPIN or 18-character 3D ULPIN.
    """
    raw_input = ulpin.strip().upper()
    
    # Extract 14-character base ULPIN
    clean_ulpin = raw_input.replace("-", "")[:14]
    
    if len(clean_ulpin) < 14:
        raise ValueError(f"ULPIN code must be at least 14 characters, received {len(raw_input)}")

    suffix_code = raw_input[14:] if len(raw_input) >= 18 else ""

    def undo_sub(s: str) -> str:
        return s.replace("Y", "I").replace("Z", "O")

    lat_1_str = undo_sub(clean_ulpin[0:2])
    lat_3_str = undo_sub(clean_ulpin[2:4])
    lat_4_str = undo_sub(clean_ulpin[4:6])

    lon_1_str = undo_sub(clean_ulpin[6:8])
    lon_3_str = undo_sub(clean_ulpin[8:10])
    lon_4_str = undo_sub(clean_ulpin[10:12])

    floor_str = undo_sub(clean_ulpin[12:14])

    lat_int = decode_base(lat_1_str, 14) - 90
    lat_frac_high = decode_base(lat_3_str, 32)
    lat_frac_low = decode_base(lat_4_str, 32)
    latitude = lat_int + (lat_frac_high * 1000 + lat_frac_low) / 1000000.0

    lon_int = decode_base(lon_1_str, 19) - 180
    lon_frac_high = decode_base(lon_3_str, 32)
    lon_frac_low = decode_base(lon_4_str, 32)
    longitude = lon_int + (lon_frac_high * 1000 + lon_frac_low) / 1000000.0

    floor_raw = decode_base(floor_str, 32)
    floor_from_code = floor_raw - 578

    # Extract vertical level from 18-char suffix if present
    vertical_level = f"Ground (F00)"
    if suffix_code:
        vertical_level = f"Vertical Unit ({suffix_code})"

    return {
        "ulpin_2d_base": clean_ulpin,
        "ulpin_3d_full": raw_input if len(raw_input) >= 18 else f"{clean_ulpin}-F00",
        "latitude": round(latitude, 6),
        "longitude": round(longitude, 6),
        "floor": floor_from_code,
        "vertical_level_desc": vertical_level,
        "is_3d": len(raw_input) >= 18 or floor_from_code != 0,
        "components": {
            "lat_integer_code": clean_ulpin[0:2],
            "lat_frac_high": clean_ulpin[2:4],
            "lat_frac_low": clean_ulpin[4:6],
            "lon_integer_code": clean_ulpin[6:8],
            "lon_frac_high": clean_ulpin[8:10],
            "lon_frac_low": clean_ulpin[10:12],
            "floor_code": clean_ulpin[12:14],
            "vertical_suffix": suffix_code
        }
    }



def get_ulpin_breakdown(latitude: float, longitude: float, floor: int = 0) -> Dict[str, Any]:
    """
    Get detailed computational breakdown for UI visualization.
    """
    latitude = round(float(latitude), 6)
    longitude = round(float(longitude), 6)
    
    lat_int = int(math.floor(latitude))
    lon_int = int(math.floor(longitude))
    lat_fraction = f"{latitude:.6f}".split(".")[1]
    lon_fraction = f"{longitude:.6f}".split(".")[1]

    lat_1_raw = encode_base(lat_int + 90, 14, 2)
    lat_3_raw = encode_base(int(lat_fraction[:3]), 32, 2)
    lat_4_raw = encode_base(int(lat_fraction[3:]), 32, 2)

    lon_1_raw = encode_base(lon_int + 180, 19, 2)
    lon_3_raw = encode_base(int(lon_fraction[:3]), 32, 2)
    lon_4_raw = encode_base(int(lon_fraction[3:]), 32, 2)

    floor_raw = encode_base(floor + 578, 32, 2)

    ulpin_code = generate_prototype_ulpin(latitude, longitude, floor)

    return {
        "ulpin": ulpin_code,
        "latitude": latitude,
        "longitude": longitude,
        "floor": floor,
        "steps": [
            {
                "segment": "Latitude Integer (0-2)",
                "calculation": f"({lat_int} + 90) = {lat_int + 90} in Base 14",
                "raw_code": lat_1_raw,
                "clean_code": ulpin_alphabet(lat_1_raw),
            },
            {
                "segment": "Latitude Frac 1-3 (2-4)",
                "calculation": f"{lat_fraction[:3]} in Base 32",
                "raw_code": lat_3_raw,
                "clean_code": ulpin_alphabet(lat_3_raw),
            },
            {
                "segment": "Latitude Frac 4-6 (4-6)",
                "calculation": f"{lat_fraction[3:]} in Base 32",
                "raw_code": lat_4_raw,
                "clean_code": ulpin_alphabet(lat_4_raw),
            },
            {
                "segment": "Longitude Integer (6-8)",
                "calculation": f"({lon_int} + 180) = {lon_int + 180} in Base 19",
                "raw_code": lon_1_raw,
                "clean_code": ulpin_alphabet(lon_1_raw),
            },
            {
                "segment": "Longitude Frac 1-3 (8-10)",
                "calculation": f"{lon_fraction[:3]} in Base 32",
                "raw_code": lon_3_raw,
                "clean_code": ulpin_alphabet(lon_3_raw),
            },
            {
                "segment": "Longitude Frac 4-6 (10-12)",
                "calculation": f"{lon_fraction[3:]} in Base 32",
                "raw_code": lon_4_raw,
                "clean_code": ulpin_alphabet(lon_4_raw),
            },
            {
                "segment": "Floor Level (12-14)",
                "calculation": f"({floor} + 578) = {floor + 578} in Base 32",
                "raw_code": floor_raw,
                "clean_code": ulpin_alphabet(floor_raw),
            }
        ]
    }


if __name__ == "__main__":
    test_lat = 25.068164
    test_lon = 85.623346
    code = generate_prototype_ulpin(test_lat, test_lon, 0)
    print(f"Generated ULPIN for ({test_lat}, {test_lon}, floor=0): {code}")
    decoded = decode_prototype_ulpin(code)
    print(f"Decoded: {decoded}")
