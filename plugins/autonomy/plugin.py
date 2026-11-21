"""Kvist be autonomous"""

import kvist


def command():
    return "autonomy"

def run(request):
    if not request:
        return kvist.read("autonomy.md") + "\n\nthis person not ask. ask only: autonomy mode activated"
    else:
        return kvist.read("autonomy.md") + "\n\nThe person asks: " + request
