"""INTERNAL AREA KNOWLEDGE BASE — area name -> every place string seen in it.

MACHINE-MAINTAINED. This file is rewritten by
Backend/Service/WhatsAppDataFetchingService/area_knowledge_service.py every
time the property pipeline learns a new place string, so any comment added
inside the dict below will be lost on the next write. Editing the ENTRIES by
hand IS supported and encouraged: fix a wrong entry, delete a junk one, or
add a place you know belongs to an area — the change is picked up on the next
backend restart and treated as knowledge the pipeline already had (it starts
counting as a hit from then on).

Each key is an area/locality. Each value is every distinct place string that
has appeared on a property the LLM filed under that area: the area's own
name, roads, landmarks, micro-localities and society/project names. One real
place often has several names ("University Road" and "VNSGU Road" are the
same road) and every one of them is kept on purpose — the point is to
recognise whatever a broker actually typed.

Comparison is done on a punctuation-and-case-insensitive key, so "VIP Road"
and "V.I.P. road" are one entry; the spelling kept here is the first one seen.

This file lives OUTSIDE Backend/ deliberately: uvicorn --reload restarts the
server on any *.py write under Backend/, which would kill the live WhatsApp
connections mid-batch every time a new place was learned.
"""

from typing import Dict, List

AREA_KNOWLEDGE_BASE: Dict[str, List[str]] = {
    '2nd Vip Road': [
        '2nd Vip Road',
    ],
    'Adajan': [
        'Adajan',
        'Adajan Pal',
        'ADAJAN SURAT',
        'Anand Mahal Road',
        'Galaxy Aventura',
        'Parshuram Garden',
        'Supath Enclave Pal',
        'Top Floor',
    ],
    'Adajan Pal': [
        'Adajan Pal',
        'Galaxy Aventura',
    ],
    'Althan': [
        'Althan',
        'Green Victory',
        'Venusia Bungalow',
    ],
    'Athwa': [
        'Athwa',
        'City Light',
        'Ghoddod Road',
        'Seema Row House',
    ],
    'Bhatar': [
        'Bhatar',
        'Crimson Palace',
        'Shree Ram Marble',
    ],
    'Canal Road': [
        'Canal Road',
        'GD Goenka',
    ],
    'Chanakyapuri': [
        'Chanakyapuri',
    ],
    'Citylight': [
        'Citylight',
        'Anurodh Dwar',
        'Chandan Park',
        'Citylight Char Rasta',
        'new City Light road',
        'Sarjan Society',
        'Shyam Mandir',
        'Shyam Mandir new City Light road',
    ],
    'Citylight Char Rasta': [
        'Citylight Char Rasta',
        'Anurodh Dwar',
    ],
    'Dandi Road': [
        'Dandi Road',
        'Silver Stone Villa',
    ],
    'Dumas': [
        'Dumas',
        'Airport',
        'Avadh Carolina',
        'Avadh Project',
    ],
    'Gauravpath Road': [
        'Gauravpath Road',
        'Siddhivinayak Height',
    ],
    'GD Goenka Canal Road': [
        'GD Goenka Canal Road',
    ],
    'Ghod Dod': [
        'Ghod Dod',
        'Ghod Dod Road',
        'Jade Blue',
        'Mira Nagar',
        'Subhash Nagar',
    ],
    'Ghod Dod Road': [
        'Ghod Dod Road',
        'Athwa',
        'City Light',
        'Jade Blue',
        'Kakadia Complex Ghod Dod Road',
        'Seema Row House',
    ],
    'Godadara': [
        'Godadara',
        'Aranya-3 by Pramukh',
    ],
    'Jahangirabad': [
        'Jahangirabad',
        'Orchid Fantasia',
    ],
    'Jahangirpura': [
        'Jahangirpura',
        'Anjani Ambrosia',
        'Vaishnodevi Sky',
        'Vaishnodevi Sky Jahangirpura',
    ],
    'Nanpura': [
        'Nanpura',
        'Nanpura Police Station',
    ],
    'Pal': [
        'Pal',
        'Adajan',
        'Arjun',
        'Arjunt',
        'Citylights',
        'Ghod Dod Road',
        'Keshav Park Society',
        'Megh Mayur Plaza',
        'Parle Point',
        'Sumeru Golden Leaf',
        'Supath Enclave',
    ],
    'Pal - Adajan': [
        'Pal - Adajan',
        'Supath Enclave',
    ],
    'Palanpur': [
        'Palanpur',
        'Raj world PALANPUR',
    ],
    'Parle Point': [
        'Parle Point',
        'Keshav Park Society',
        'Megh Mayur Plaza',
        'Rudravan Apartment',
    ],
    'Piplod': [
        'Piplod',
        'Aayushi Niwas',
        'Dumas Road',
        'Green Serene',
        'Gymkhana Road',
        'Hari Om Bungalows',
        'Himgiri Bungalows',
        'Keshav Nagar',
        'Milan Bungalow',
        'Passport Office',
        'Piplod (Near Ryan International School',
        'PIPLOD Dumas Road',
        'Piplod Main Road',
        'Piplod–Vesu',
        'RahulRaj Mall',
        'Rajhans Cinema',
        'Ryan International School',
        'S D Jain School',
        'Saraswat Nagar Society',
        'Shreedhar Row House',
        'Tirupati Nagar',
        'Vacanza',
        'Vacanza Bungalows',
        'Vijay Sales',
    ],
    'Piplod–Vesu': [
        'Piplod–Vesu',
        'Vacanza',
    ],
    'Udhana Darwaja': [
        'Udhana Darwaja',
    ],
    'Udhna': [
        'Udhna',
        'Udhna Magdalla',
    ],
    'Udhna Magdalla': [
        'Udhna Magdalla',
    ],
    'Ugat Canal Road': [
        'Ugat Canal Road',
        'Sagar Sankul',
    ],
    'Vadod': [
        'Vadod',
        'Aakash Homes',
    ],
    'Vesu': [
        'Vesu',
        '2nd Vip Road',
        'Aashirvad Avenue',
        'Avadh Carolina - Dumas',
        'Dumas',
        'Happy Residency',
        'Mangalam Heights',
        'Mangalam Heights VESU',
        'Nandini Residency',
        'Olive Club',
        'Palm Avenue',
        'PIPLOD',
        'Punyabhoomi',
        'Rebounce',
        'Sevion circle',
        'Shilp Residency',
        'Shivkrupa',
        'SHUBH ENCLAVE',
        'Shyam Baba Temple',
        'Udhana Magdalla Road VESU',
        'University road',
        'Vanilla Sky',
        'VIP Road',
    ],
    'Village Tena': [
        'Village Tena',
        'Block No. 47',
        'Rama Paper Staff Colony',
        'Sunrise Glass Factory',
        'Taluka & District Surat',
        'Village Tena (Barbodan Dandi road',
    ],
    'VIP Road': [
        'VIP Road',
        'Vanilla Sky',
    ],
}
