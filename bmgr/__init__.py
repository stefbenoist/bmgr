import os, logging

from flask import Flask, jsonify
from . import server

def get_int_param(app, name, default):
    val = os.environ.get(name)
    if val is not None:
        return int(val)
    return int(app.config.get(name, default))

def get_bool_param(app, name, default):
    val = os.environ.get(name)
    if val is not None:
        return val.lower() in ("1", "true", "yes")
    return app.config.get(name, default)

def create_app(test_config=None):
    # create and configure the app
    app = Flask(__name__, instance_relative_config=True)

    # Logger
    logger = logging.getLogger(__name__)

    # Health check endpoint
    @app.route('/health', methods=["GET"])
    def health():
        resp = jsonify({"status": "passing"})
        resp.status_code = 200
        return resp

    # Outside of tests, conf is passed via a file, by default /etc/bmgr/bmgr.conf
    # or the location specified in BMGR_CONF_FILE
    if test_config is None:
        app.config.from_envvar('BMGR_CONF_FILE', silent=True) or \
            app.config.from_pyfile('/etc/bmgr/bmgr.conf', silent=True)
    else:
        # load the test config if passed in
        app.config.from_mapping(test_config)

    db_uri = (os.environ.get('BMGR_DB_URI', None) or
              app.config.get('BMGR_DB_URI', None))

    # BUILD the database URI unless explicitly specified
    if db_uri:
        app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
    else:
        try:
            app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql://{0}:{1}@{2}/bmgr'.format(
                os.environ.get('BMGR_DB_USER', None) or app.config['BMGR_DB_USER'],
                os.environ.get('BMGR_DB_PASS', None) or app.config['BMGR_DB_PASS'],
                os.environ.get('BMGR_DB_HOST', None) or app.config['BMGR_DB_HOST'])

        except KeyError:
            raise ValueError("Please provide a database host and user/password "
                             "or URI")

    # DB Pool connection config
    pool_size = get_int_param(app,'BMGR_DB_POOL_SIZE', 20)
    pool_recycle = get_int_param(app,'BMGR_DB_POOL_RECYCLE', 600)

    recursive_rendering = get_bool_param(app,'BMGR_ENABLE_RECURSIVE_RENDERING', True)
    app.config.setdefault('BMGR_TEMPLATE_PATH', '/etc/bmgr/templates/')
    app.config.setdefault('BMGR_JINJA_CUSTOMS_PACKAGE_PATH', 'customs')
    app.config.setdefault('SQLALCHEMY_ENGINE_OPTIONS', {'pool_size': pool_size, 'pool_recycle': pool_recycle})
    app.config.setdefault('SQLALCHEMY_TRACK_MODIFICATIONS', False)
    app.register_blueprint(server.bp)

    # ensure the instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    # Initialize the SQLAlchemy db object
    server.db.init_app(app)

    # Initialize the jinja2 env
    server.create_jinja_env(app.config.get('BMGR_TEMPLATE_PATH'),
                            app.config.get('BMGR_JINJA_CUSTOMS_PACKAGE_PATH'),
                            recursive_rendering)

    # Initialize the db and data if present in conf
    init_data = app.config.get('BMGR_INIT_DATA', [])

    # Load templates
    server.load_templates()

    # Log config
    logger.info(app.config)

    @app.cli.command(help='Intialize the bmgr database')
    def initdb():
        server.init_db(init_data)

    return app
