from secrets import choice
from string import ascii_uppercase, digits


def make_order_reference():
    alphabet = ascii_uppercase + digits
    return "KM-" + "".join(choice(alphabet) for _ in range(8))
