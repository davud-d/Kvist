"""Plugin that teaches users to code instead of writing it for them"""

import kvist


def command():
    """Returns the plugin identifier"""
    return "novibecode"


def run(request):
    """Process user request and return instruction with optional context"""
    instruct = kvist.read("novibecode.md")

    if not request.strip():
        return instruct

    return instruct + "\n\nThe person asks: " + request
