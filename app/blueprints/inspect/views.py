from app import app
from app.models import LibraryEngine
from app.utils import user_utils, translation_utils
from flask import Blueprint, render_template, request, jsonify

inspect_blueprint = Blueprint('inspect', __name__, template_folder='templates')

translators = translation_utils.TranslationUtils()

@inspect_blueprint.route('/')
@inspect_blueprint.route('/details')
def inspect_index():
    engines = LibraryEngine.query.filter_by(user_id = user_utils.get_uid()).all()
    return render_template('details.inspect.html.jinja2', page_name='inspect_details', engines=engines)

@inspect_blueprint.route('/compare')
def inspect_compare():
    engines = LibraryEngine.query.filter_by(user_id = user_utils.get_uid()).all()
    return render_template('compare.inspect.html.jinja2', page_name='inspect_compare', engines=engines)

@inspect_blueprint.route('/access')
def inspect_access():
    engines = LibraryEngine.query.filter_by(user_id = user_utils.get_uid()).all()
    return render_template('access.inspect.html.jinja2', page_name='inspect_access', engines=engines)

@inspect_blueprint.route('/leave', methods=['POST'])
def translate_leave():
    translators.deattach(user_utils.get_uid())
    return "0"

@inspect_blueprint.route('/attach_engine/<id>')
def translate_attach(id):
    if translators.launch(user_utils.get_uid(), id, True):
        return "0"
    else:
        return "-1"

@inspect_blueprint.route('/get/<text>')
def inspect_get(text):
    translation = translators.get_inspect(user_utils.get_uid(), text)
    return jsonify(translation) if translation else "-1"