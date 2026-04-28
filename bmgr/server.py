from flask import (
  Blueprint, jsonify, make_response, abort, g, current_app, request
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import joinedload, relationship, synonym, validates
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import UniqueConstraint, and_, update, select
from flask_expects_json import expects_json
from ClusterShell.NodeSet import NodeSet as nodeset
from jinja2 import Environment, FileSystemLoader

import json, jinja2, sys, itertools, pathlib, re, importlib

MAX_NODESET = 100000

bp = Blueprint('main', __name__)
db = SQLAlchemy()

host_profiles_table = db.Table('host_profiles',
                               db.Column('host_id', db.Integer,
                                         db.ForeignKey('hosts.id')),
                               db.Column('profile_id', db.Integer,
                                         db.ForeignKey('profiles.id'))
                               )

# Declare jinja_env as Global in order to gain performances (also take benefit of default jinja2 memory cache)
jinja_env: Environment

def load_jinja_customs(path):
  """
  Load jinja2 customs filters anf globals from directory path
  """
  filters = {}
  globals_ = {}

  base = pathlib.Path(path)
  if not base.exists():
    return filters, globals_

  for file in base.rglob("*.py"):
    if file.name.startswith("_"):
      continue

    module_name = f"bmgr_jinja_ext_{file.relative_to(base).with_suffix('').as_posix().replace('/', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if hasattr(module, "FILTERS"):
      filters.update(module.FILTERS)

    if hasattr(module, "GLOBALS"):
      globals_.update(module.GLOBALS)

  return filters, globals_

def create_jinja_env(template_path, jinja_customs_path, enable_recursive_rendering=False):
  """Create jinja Env"""
  global jinja_env

  jinja_env = Environment(
    loader=FileSystemLoader(template_path)
  )

  # Load jinja2 customs filters and globals
  filters, globals_ = load_jinja_customs(jinja_customs_path)
  jinja_env.filters.update(filters)
  jinja_env.globals.update(globals_)

  jinja_env.enable_recursive_rendering = enable_recursive_rendering

def load_templates():
    for t in jinja_env.list_templates():
      jinja_env.get_template(t)

class CollectionMeta(db.Model):
  """
  Class used for incrementing revision for Etag handling
  """
  __tablename__ = "collections_meta"

  name = db.Column(db.String(255), primary_key=True)
  revision = db.Column(db.Integer, nullable=False, default=1)

def bump_collection_revisions(session, name):
  """
  Bump revision for all collections impacted by Write operations
  """
  # Apply a lock on defined record
  session.execute(
    select(CollectionMeta)
    .where(CollectionMeta.name == name)
    .with_for_update()
  )

  session.execute(
    update(CollectionMeta)
    .where(CollectionMeta.name == name)
    .values(revision=CollectionMeta.revision + 1)
  )

class Profile(db.Model):
  __tablename__ = 'profiles'
  __collection_name__ = 'profiles'
  id = db.Column(db.Integer, primary_key=True)
  name = db.Column(db.String(255), unique=True)
  weight = db.Column(db.Integer, default=0)
  _attributes = db.Column('attributes', db.String())

  @property
  def attributes(self):
    return json.loads(self._attributes)

  @attributes.setter
  def attributes(self, value):
    self._attributes = json.dumps(value)

  attributes = synonym('_attributes', descriptor=attributes)

  def __init__(self, name, attributes=None, weight=0):
      if attributes is None:
          attributes = {}
      self.name = name
      self._attributes = json.dumps(attributes)
      self.weight = weight

  def __repr__(self):
      return '<Profile %r>' % self.name

  def to_dict(self):
    return {'name': self.name,
            'attributes': self.attributes,
            'weight': self.weight}

  @classmethod
  def from_dict(cls, d):
    profile = cls(d['name'], d.get('attributes', {}), d.get('weight', 0))
    return profile

  def __lt__(self, other):
    return (- self.weight, self.name) < (- other.weight, other.name)

def json_abort(status, error):
  abort(make_response(jsonify(error=error), status))


def parse_template_uri(uri):
  m = re.match('file://(.*)', uri)
  if m is None:
    json_abort(400, 'Unable to parse template URI')

  return m.group(1)

class Host(db.Model):
  __tablename__ = 'hosts'
  __collection_name__ = 'hosts'
  id = db.Column(db.Integer, primary_key=True)
  hostname = db.Column(db.String(255), unique=True)

  profiles = relationship("Profile", backref="host",
                       secondary=host_profiles_table, order_by=Profile.weight)

  aliases = relationship("Alias", backref="host", cascade="all, delete-orphan")

  def __init__(self, hostname):
    self.hostname = hostname

  def __repr__(self):
    return '<Host %r>' % self.hostname

  @property
  def attributes(self):
    r = {u'hostname': self.hostname}
    r.update(merge_profile_attributes(self.profiles))

    return r

  @classmethod
  def from_dict(cls, d):
    host = cls(d['name'])
    if 'profiles' in d:
      host.profiles = [ get_profile(p) for p in d['profiles'] ]

    return host

def merge_profile_attributes(profiles):
    # TODO: We should look at deep merging
    r = {}
    for p in profiles:
      r.update(p.attributes)

    return r

class Resource(db.Model):
  __tablename__ = 'resources'
  id = db.Column(db.Integer, primary_key=True)
  name = db.Column(db.String(255), unique=True)
  template_uri = db.Column(db.String(4096))

  @validates('template_uri')
  def validate_uri(self, key, template_uri):
    parse_template_uri(template_uri)
    return template_uri

  def __init__(self, name, template_uri):
    self.name = name
    self.template_uri = template_uri

  def __repr__(self):
      return '<Resource %r>' % self.name

  def to_dict(self):
    return {'name': self.name,
            'template_uri': self.template_uri}

  @classmethod
  def from_dict(cls, d):
    profile = cls(d['name'], d['template_uri'])
    return profile

class Alias(db.Model):
  __tablename__ = 'aliases'
  __table_args__ = (UniqueConstraint('name', 'host_id', name='uix_1'),)
  id = db.Column(db.Integer, primary_key=True)
  name = db.Column(db.String(255))
  target_id = db.Column(db.Integer, db.ForeignKey('resources.id'))
  host_id = db.Column(db.Integer, db.ForeignKey('hosts.id'))
  autodelete = db.Column(db.Boolean)

  target = relationship("Resource", uselist=False)

  def __init__(self, name, target, host, autodelete=False):
    self.name = name
    self.target = target
    self.host = host
    self.autodelete = autodelete

  def __repr__(self):
    return '<Alias %r>' % self.name

@bp.errorhandler(404)
def not_found(error):
  return make_response(jsonify({'error': 'Not found'}), 404)

@bp.errorhandler(405)
def unauthorized(error):
  return make_response(jsonify({'error': 'Unauthorized'}), 405)

@bp.errorhandler(409)
def conflict(error):
  return make_response(jsonify({'error': 'Conflict'}), 409)

def init_db(data):
  db.create_all()

  # Init revisions values
  for name in ['hosts', 'profiles']:
    if not db.session.query(CollectionMeta).filter_by(name=name).first():
      db.session.add(CollectionMeta(name=name, revision=1))

  if not data:
    return

  resources = [e for e in data if e.get("type") == "resource"]
  aliases = [e for e in data if e.get("type") == "alias"]

  # Create firstly resources as aliases depend on them if not exist
  for res in resources:
    if get_resource(res.get('name'), True) is None:
      db.session.add(Resource(res.get('name'), res.get('template_uri')))

  # Create next aliases if not exist
  for alias in aliases:
    r = get_resource(alias.get('target'))
    if get_alias(alias.get('name'), None, True) is None:
      db.session.add(Alias(alias.get('name'), r, None))

  db.session.commit()

def get_profile(profile_name):
  p = None
  try:
    p = db.session.query(Profile).filter_by(name=profile_name).one()
  except NoResultFound:
    json_abort(404, "Profile '{}' not found".format(profile_name))

  return p

def get_resource(resource_name, allow_fail=False):
  r = None
  try:
    r = db.session.query(Resource).filter_by(name=resource_name).one()
  except NoResultFound:
    if allow_fail:
      return None
    else:
      json_abort(404, "Resource '{}' not found".format(resource_name))

  return r

def get_alias(alias_name, hostname=None, allow_fail=False):
  try:
    a = db.session.query(Alias)
    if hostname:
      return a.join(Host).filter(and_(Alias.name==alias_name,
                                      Host.hostname==hostname)).one()
    else:
      return a.filter(and_(Alias.name==alias_name,
                           Alias.host==None)).one()
  except NoResultFound:
    if allow_fail:
      return None
    else:
      json_abort(404, "Alias '{}' not found".format(alias_name))

def render(tpl, context):
  path = parse_template_uri(tpl)

  if jinja_env.enable_recursive_rendering:
    # Maximum recursive depth
    max_depth = 3
    prev = jinja_env.get_template(path).render(context)
    for depth in range(max_depth):
      curr = jinja_env.from_string(prev).render(context)
      if curr == prev:
        return curr
      prev = curr

  return jinja_env.get_template(path).render(context)

def delete_profile(name):
  p = get_profile(name)
  db.session.delete(p)

def query_hosts(host_list=None, check_count=False):
  hosts = db.session.query(Host).options(joinedload(Host.profiles))

  if host_list is not None:
      hosts = hosts.filter(Host.hostname.in_(host_list))
      if check_count and hosts.count() != len(host_list):
        json_abort(404, "Host not found")

  return hosts


def query_aliases(alias_name=None, host_list=None, check_count=False):
  aliases = db.session.query(Alias)

  if alias_name is not None:
    aliases = aliases.filter_by(name=alias_name)

  if host_list is not None:
    aliases = aliases.join(Host).filter(Host.hostname.in_(host_list))

  if alias_name and host_list and check_count:
      if aliases.count() != len(host_list):
        json_abort(404, "Alias or override not found")

  return aliases

def get_hosts_folded(host_list=None):
  hosts = query_hosts(host_list)
  folded_list = []

  for profiles, group in itertools.groupby(sorted(hosts, key = lambda h: h.profiles),
                                           lambda x: x.profiles):
    folded_list.append({
        'name': str(nodeset.fromlist([h.hostname for h in group])),
        'profiles': [p.name for p in sorted(profiles)],
        'attributes': merge_profile_attributes(profiles)})

  return sorted(folded_list, key = lambda x: x['profiles'])

def delete_hosts(host_list):
  db_hosts = query_hosts(host_list, check_count=True)

  for dbh in db_hosts:
    db.session.delete(dbh)

def get_host(hostname):
  """ Returns a single Host """
  try:
    return query_hosts([hostname]).one()
  except NoResultFound:
    json_abort(404, "Host '{}' not found".format(hostname))

@bp.route('/api/v1.0/hosts', methods=['POST'])
@expects_json({
  'type': 'object',
  'properties': {
    'name': {'type': 'string'},
    'profiles': {'type': 'array', 'items': {'type': 'string'}},
  },
  'required': ['name']
})
def api_hosts_post():
  host_list = []
  try:
    host_list = nodeset(g.data['name'])
    if len(host_list) > MAX_NODESET:
      json_abort(413, "Nodeset too large")

    for i, host in enumerate(host_list):
      single_host = g.data
      single_host['name'] = host
      db_host = Host.from_dict(single_host)
      db.session.add(db_host)
      if i%1000 == 0:
        db.session.flush()

    bump_collection_revisions(db.session, "hosts")
    db.session.commit()
  except SQLAlchemyError as e:
    json_abort(409, str(e.__dict__['orig']))

  folded_hosts = get_hosts_folded(host_list)
  return jsonify(folded_hosts)

@bp.route('/api/v1.0/hosts', methods=['GET'])
def api_hosts_get():
  # Get revision
  meta = db.session.get(CollectionMeta, 'hosts')
  etag = f"hosts:rev{meta.revision}"

  # return Status Not Modified (304) if Entity tag matches
  if request.if_none_match.contains_weak(etag):
    return "", 304

  response = make_response(jsonify(get_hosts_folded()))
  response.headers["ETag"] = f'W/"{etag}"'
  response.headers["Cache-Control"] = "private, must-revalidate"

  return response

@bp.route('/api/v1.0/hosts/<string:hostname>', methods=['DELETE'])
def api_hosts_hostname_delete(hostname):
  delete_hosts(nodeset(hostname))
  bump_collection_revisions(db.session, "hosts")
  db.session.commit()
  return make_response(jsonify([]), 204)

@bp.route('/api/v1.0/hosts/<string:hostname>', methods=['PATCH'])
@expects_json({
  'type': 'object',
  'properties': {
    'profiles': {'type': 'array', 'items': {'type': 'string'}},
  },
})
def api_hosts_hostname_patch(hostname):
  need_commit = False
  nodelist=nodeset(hostname)
  hosts = query_hosts(nodelist, check_count=True)
  profiles = []

  if 'profiles' in g.data:
    for p in g.data['profiles']:
      profiles.append(get_profile(p))

    for host in hosts:
      host.profiles = profiles

    need_commit = True

  if need_commit:
    bump_collection_revisions(db.session, "hosts")
    db.session.commit()

  return jsonify(get_hosts_folded(nodelist))

@bp.route('/api/v1.0/hosts/<string:hostname>', methods=['GET'])
def api_hosts_hostname_get(hostname):
  return jsonify(get_hosts_folded(nodeset(hostname)))

@bp.route('/api/v1.0/profiles', methods=['POST'])
@expects_json({
  'type': 'object',
  'properties': {
    'name': {'type': 'string'},
    'attributes': {'type': 'object'},
    'weight': {'type': 'integer'},
  },
  'required': ['name']
})
def api_profiles_post():
  profile = None
  try:
    profile = Profile.from_dict(g.data)
    db.session.add(profile)
    bump_collection_revisions(db.session, "profiles")
    db.session.commit()
  except SQLAlchemyError as e:
    json_abort(409, str(e.__dict__['orig']))

  return jsonify(profile.to_dict())

@bp.route('/api/v1.0/profiles', methods=['GET'])
def api_profiles_get():
  # Get revision
  meta = db.session.get(CollectionMeta, 'profiles')
  etag = f"profiles:rev{meta.revision}"

  # return Status Not Modified (304) if Entity tag matches
  if request.if_none_match.contains_weak(etag):
    return "", 304

  profiles = db.session.query(Profile).all()
  r = []
  for p in profiles:
    r.append(p.to_dict())

  response = make_response(jsonify(r))
  response.headers["ETag"] = f'W/"{etag}"'
  response.headers["Cache-Control"] = "private, must-revalidate"

  return response


@bp.route('/api/v1.0/profiles/<string:name>', methods=['DELETE'])
def api_profiles_profile_delete(name):
  delete_profile(name)
  bump_collection_revisions(db.session, "profiles")
  db.session.commit()
  return make_response(jsonify({}), 204)


@bp.route('/api/v1.0/profiles/<string:name>', methods=['GET'])
def api_profiles_profile_get(name):
  profile = get_profile(name)
  return jsonify(profile.to_dict())

@bp.route('/api/v1.0/profiles/<string:name>', methods=['PATCH'])
@expects_json({
  'type': 'object',
  'properties': {
    'attributes': {'type': 'object'},
    'weight': {'type': 'integer'},
  },
})
def api_profiles_profile_patch(name):
  need_commit = False
  profile = get_profile(name)

  if 'attributes' in g.data:
    profile.attributes = g.data['attributes']
    need_commit = True

  if 'weight' in g.data:
    profile.weight = g.data['weight']
    need_commit = True

  if need_commit:
    bump_collection_revisions(db.session, "profiles")
    db.session.commit()

  return make_response(jsonify(profile.to_dict()), 200)

@bp.route('/api/v1.0/resources', methods=['GET'])
def api_resources_get():
  resources = db.session.query(Resource).all()
  result = []
  for r in resources:
    result.append(r.to_dict())

  return jsonify(result)

@bp.route('/api/v1.0/resources', methods=['POST'])
@expects_json({
  'type': 'object',
  'properties': {
    'name': {'type': 'string'},
    'template_uri': {'type': 'string'},
  },
  'required': ['name', 'template_uri']
})
def api_resources_post():
  resource = None
  try:
    resource = Resource.from_dict(g.data)
    db.session.add(resource)
    db.session.commit()
  except SQLAlchemyError as e:
    json_abort(409, str(e.__dict__['orig']))

  return jsonify(resource.to_dict())

@bp.route('/api/v1.0/resources/<string:name>', methods=['PATCH'])
@expects_json({
  'type': 'object',
  'properties': {
    'template_uri': {'type': 'string'},
  }
})
def api_resources_resource_patch(name):
  need_commit = False
  resource = get_resource(name)

  if 'template_uri' in g.data:
    resource.template_uri = g.data['template_uri']
    need_commit = True

  if need_commit:
    db.session.commit()

  return make_response(jsonify(resource.to_dict()), 200)

@bp.route('/api/v1.0/resources/<string:name>', methods=['GET'])
def api_resources_resource_get(name):
  return jsonify(get_resource(name).to_dict())

@bp.route('/api/v1.0/resources/<string:name>', methods=['DELETE'])
def api_resources_resource_delete(name):
  resource = get_resource(name)
  db.session.delete(resource)
  db.session.commit()
  return make_response(jsonify({}), 204)

@bp.route('/api/v1.0/resources/<string:name>/<string:hostname>', methods=['GET'])
def api_resources_resource_render(name, hostname):
  a = get_alias(name, hostname, allow_fail=True)
  if not a:
    a = get_alias(name, allow_fail=True)

  if a:
    resource = a.target
  else:
    resource = get_resource(name)

  if a and a.autodelete:
    db.session.delete(a)
    db.session.commit()

  host = get_host(hostname)

  try:
    return make_response(render(resource.template_uri, host.attributes))
  except jinja2.exceptions.TemplateNotFound as e:
    json_abort(400, 'Template not found on server: ' + str(e))
  except jinja2.exceptions.TemplateError as e:
    json_abort(400, 'Error while rendering template: ' + str(e))


@bp.route('/api/v1.0/aliases', methods=['POST'])
@expects_json({
  'type': 'object',
  'properties': {
    'name': {'type': 'string'},
    'target': {'type': 'string'},
  },
  'additionalProperties': False,
  'required': ['name', 'target']
})
def api_aliases_post():
  if query_aliases(g.data['name']).count() > 0:
    json_abort(409, "Alias already exists")

  target = get_resource(g.data['target'])
  a = Alias(g.data['name'], target, None)

  db.session.add(a)
  db.session.commit()

  #TODO
  return jsonify({})

@bp.route('/api/v1.0/aliases/<string:name>', methods=['POST'])
@expects_json({
  'type': 'object',
  'properties': {
    'hosts': {'type': 'string'},
    'target': {'type': 'string'},
    'autodelete': {'type': 'boolean'},
  },
  'required': ['hosts', 'target']
})
def api_aliases_alias_post(name):
  query_aliases(name)

  # Check if the main alias is defined
  get_alias(name)

  if query_aliases(name, nodeset(g.data['hosts'])).count() > 0:
    json_abort(409, "Alias or override already exists")

  target = get_resource(g.data['target'])
  for i,host in enumerate(query_hosts(nodeset(g.data['hosts']),
                                      check_count=True).all()):
    a = Alias(name,
              target,
              host,
              g.data.get('autodelete', False))

    db.session.add(a)
    if i%1000 == 0:
      db.session.flush()

  db.session.commit()

  return jsonify({})

@bp.route('/api/v1.0/aliases/<string:name>', methods=['DELETE'])
def api_aliases_alias_delete(name):

  for a in query_aliases(name).all():
    db.session.delete(a)

  db.session.commit()
  return make_response(jsonify({}), 204)

@bp.route('/api/v1.0/aliases/<string:name>/<string:hostname>', methods=['DELETE'])
def api_aliases_alias_host_delete(name, hostname):

  for a in query_aliases(name, nodeset(hostname),
                         check_count=True).all():
    db.session.delete(a)

  db.session.commit()
  return make_response(jsonify({}), 204)

def alias_to_dict(name=None, merge=True):
  r = {}
  for a in query_aliases(name).all():
    r.setdefault(a.name, {'name': a.name,
                          'overrides': {} })
    if a.host:
      r[a.name]['overrides'][a.host.hostname] = {'target': a.target.name,
                                                 'autodelete': a.autodelete}
    else:
      r[a.name]['target'] = a.target.name

  if merge:
    merged_res = {}
    for a in r.keys():
      merged_overrides = {}

      #Group hosts with the same overrides
      sorted_overrides = sorted(r[a]['overrides'].items(),
                                key=lambda x: (x[1]['target'],
                                               x[1]['autodelete']))

      for status, group in itertools.groupby(sorted_overrides,
                                              lambda x: x[1]):
        merged_overrides[str(nodeset.fromlist([h[0] for h in group]))] = status

      merged_res[a] = { 'name': a,
                        'target': r[a]['target'],
                        'overrides':  merged_overrides }
    r = merged_res
  return r

@bp.route('/api/v1.0/aliases', methods=['GET'])
def api_aliases_get():
  r = alias_to_dict()
  return jsonify(list(r.values()))

@bp.route('/api/v1.0/aliases/<string:name>', methods=['GET'])
def api_aliases_alias_get(name):
  alias = list(alias_to_dict(name).values())
  if alias:
    return jsonify(alias[0])
  else:
    json_abort(404, "Alias not found")

if __name__ == "__main__":
  if sys.argv[1] == 'initdb':
    init_data = current_app.config.get("BMGR_INIT_DATA", [])
    init_db(init_data)
