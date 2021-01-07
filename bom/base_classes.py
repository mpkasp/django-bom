from collections import OrderedDict
from django.forms.models import model_to_dict


class AsDictModel:
    def as_dict(self):
        try:
            return model_to_dict(self)
        except (TypeError, AttributeError):
            return dict(self)

    def __iter__(self):
        for key in dir(self):
            if not key.startswith("_") and not key == "objects":
                value = getattr(self, key)
                if not callable(value):
                    if isinstance(value, (dict, OrderedDict)):
                        for subkey, subvalue in value.items():
                            try:
                                value[subkey] = subvalue.as_dict()
                            except AttributeError:
                                pass
                        yield key, value
                    else:
                        try:
                            yield key, value.as_dict()
                        except AttributeError:
                            if isinstance(value, (int, float, complex, bool)):
                                yield key, value
                            else:
                                yield key, str(value)
