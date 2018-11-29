"""Exceptions raised in Thoth-dependency monkey implementation"""

class NotFoundError(BadRequest):
    """Exception raised if a Validation does not exist.

    Attributes:
        id -- id of the Validation that does not exist
        message -- verbal representation
    """

    def __init__(self, id):
        self.id = id
        self.message = "Validation {} doesn't exist".format(id)


class IncorrectStackFormatError(Exception):
    """Exception raised if the inputted software stack is not in the correct format.
    """

    def __init__(self, string):  # pragma: no cover
        self.message = "{}".format(string)

    def str(self):  # pragma: no cover
        return self.message
