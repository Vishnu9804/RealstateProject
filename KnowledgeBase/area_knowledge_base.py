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
    'Adajan': [
        'Adajan',
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
    'Citylight': [
        'Citylight',
        'Chandan Park',
        'Sarjan Society',
    ],
    'Ghod Dod': [
        'Ghod Dod',
        'Mira Nagar',
        'Subhash Nagar',
    ],
    'Ghod Dod Road': [
        'Ghod Dod Road',
        'Jade Blue',
    ],
    'Godadara': [
        'Godadara',
        'Aranya-3 by Pramukh',
    ],
    'Pal': [
        'Pal',
        'Adajan',
        'Citylights',
        'Ghod Dod Road',
        'Keshav Park Society',
        'Megh Mayur Plaza',
        'Parle Point',
        'Supath Enclave',
    ],
    'Parle Point': [
        'Parle Point',
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
    'Udhna': [
        'Udhna',
        'Udhna Magdalla',
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
        'Nandini Residency',
        'Olive Club',
        'Palm Avenue',
        'Punyabhoomi',
        'Rebounce',
        'Sevion circle',
        'Shilp Residency',
        'Shivkrupa',
        'SHUBH ENCLAVE',
        'Shyam Baba Temple',
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
}
