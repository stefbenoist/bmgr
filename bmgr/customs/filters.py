import json, yaml, re

def regex_replace(s, find, replace):
  """Implementation of a regex filter"""
  return re.sub(find, replace, s)

FILTERS = {
    "from_json": json.loads,
    "from_yaml": yaml.safe_load,
    "regex_replace": regex_replace,
}
