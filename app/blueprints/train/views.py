from app import app, db
from app.flash import Flash
from app.models import LibraryCorpora, LibraryEngine, Engine, File, Corpus_Engine, Corpus, User, Corpus_File
from app.utils import user_utils, utils, data_utils, tensor_utils, tasks
from app.utils.trainer import Trainer
from app.utils.power import PowerUtils
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, send_file
from flask_login import login_required
from sqlalchemy import func
from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
import namegenerator
import datetime
from werkzeug.datastructures import FileStorage
from celery.result import AsyncResult
from functools import reduce

import hashlib
import os
import yaml
import shutil
import sys
import ntpath
import subprocess
import glob
import pynvml
import re
import json


train_blueprint = Blueprint('train', __name__, template_folder='templates')

@train_blueprint.route('/')
@utils.condec(login_required, user_utils.isUserLoginEnabled())
def train_index():
    if user_utils.is_normal(): return redirect(url_for('index'))

    currently_training = Engine.query.filter_by(uploader_id = user_utils.get_uid()) \
                            .filter(Engine.status.like("training")).all()

    if (len(currently_training) > 0):
        return redirect(url_for('train.train_console', id=currently_training[0].id))

    random_name = namegenerator.gen()
    tryout = 0
    while len(Engine.query.filter_by(name = random_name).all()):
        random_name = namegenerator.gen()
        tryout += 1

        if tryout >= 5:
            random_name = ""
            break

    random_name = " ".join(random_name.split("-")[:2])

    pynvml.nvmlInit()
    gpus = list(range(0, pynvml.nvmlDeviceGetCount()))
    library_corpora = user_utils.get_user_corpora().filter(LibraryCorpora.corpus.has(Corpus.type == "bilingual")).all()
    corpora = [c.corpus for c in library_corpora]

    return render_template('train.html.jinja2', page_name='train', page_title='Train',
                            corpora=corpora, random_name=random_name,
                            gpus=gpus)

@train_blueprint.route('/start', methods=['POST'])
@utils.condec(login_required, user_utils.isUserLoginEnabled())
def train_start():
    if user_utils.is_normal(): return url_for('index')
    engine_path = os.path.join(user_utils.get_user_folder("engines"), utils.normname(user_utils.get_user().username, request.form['nameText']))
    task = tasks.launch_training.apply_async(args=[user_utils.get_uid(), engine_path, { i[0]: i[1] if i[0].endswith('[]') else i[1][0] for i in request.form.lists()}])

    return jsonify({ "result": 200, "task_id": task.id })

@train_blueprint.route('/launch_status', methods=['POST'])
@utils.condec(login_required, user_utils.isUserLoginEnabled())
def launch_status():
    task_id = request.form.get('task_id')
    result = tasks.launch_training.AsyncResult(task_id)

    if result and result.status == "SUCCESS":
        engine_id = result.get()
        if engine_id != -1:
            return jsonify({ "result": 200, "engine_id": result.get() })
        else:
            return jsonify({ "result": -2 })
    else:
        return jsonify({ "result": -1 })

@train_blueprint.route('/launch', methods=['POST'])
@utils.condec(login_required, user_utils.isUserLoginEnabled())
def train_launch():
    id = request.form.get('engine_id')
    if user_utils.is_normal(): return url_for('index')

    task_id, monitor_task_id = Trainer.launch(user_utils.get_uid(), id)

    return url_for('train.train_console', id=id)

@train_blueprint.route('/console/<id>')
@utils.condec(login_required, user_utils.isUserLoginEnabled())
def train_console(id):
    if user_utils.is_normal(): return redirect(url_for('index'))
    
    engine = Engine.query.filter_by(id = id).first()
    config_file_path = os.path.join(os.path.realpath(os.path.join(app.config['PRELOADED_ENGINES_FOLDER'], engine.path)), 'config.yaml')
    config = None

    try:
        with open(config_file_path, 'r') as config_file:
            config = yaml.load(config_file, Loader=yaml.FullLoader)
    except:
        pass

    launched = datetime.datetime.timestamp(engine.launched)
    finished = datetime.datetime.timestamp(engine.finished) if engine.finished else None
    elapsed = (finished - launched) if engine.finished else None

    corpora_raw = Corpus_Engine.query.filter_by(engine_id = engine.id, is_info = True).all()

    corpora = {}
    for corpus_entry in corpora_raw:
        if corpus_entry.phase in corpora:
            corpora[corpus_entry.phase].append(corpus_entry.corpus)
        else:
            corpora[corpus_entry.phase] = [corpus_entry.corpus]


    return render_template("train_console.html.jinja2", page_name="train",
            engine=engine, config=config,
            launched = launched, finished = finished,
            elapsed = elapsed, corpora=corpora, elapsed_format=utils.seconds_to_timestring(elapsed) if elapsed else None)

@train_blueprint.route('/graph_data', methods=["POST"])
@utils.condec(login_required, user_utils.isUserLoginEnabled())
def train_graph():
    if user_utils.is_normal(): return jsonify([])

    tags = request.form.getlist('tags[]')
    id = request.form.get('id')
    last_raw = request.form.get('last')
    last = json.loads(last_raw)

    engine = Engine.query.filter_by(id=id).first()

    stats = {}
    for tag in tags:
        data = tensor_utils.get_tag(id, tag)
        if data:
            stats[tag] = []
            for item in data:
                if item.step % 100 == 0:
                    stats[tag].append({ "time": item.wall_time, "step": item.step, "value": item.value })
            
            # The first step contains the initial learning rate which is
            # normally way bigger than the next one and it makes the chart
            # look like a straight line
            if tag == "train/train_learning_rate":
                stats[tag] = stats[tag][1:]

    return jsonify({ "stopped": engine.has_stopped(), "stats": stats })

@train_blueprint.route('/train_status', methods=["POST"])
@utils.condec(login_required, user_utils.isUserLoginEnabled())
def train_status():
    if user_utils.is_normal(): return jsonify([])

    id = request.form.get('id')

    engine = Engine.query.filter_by(id = id).first()
    tensor_path = os.path.join(engine.path, "model/tensorboard")
    files = glob.glob(os.path.join(tensor_path, "*"))
    
    if len(files) > 0:
        log = files[0]

        eacc = EventAccumulator(log)
        eacc.Reload()

        stats = {}

        try:
            epoch_no = 0
            for data in eacc.Scalars("train/epoch"):
                if data.value > epoch_no:
                    epoch_no = data.value
            stats["epoch"] = epoch_no
        except:
            pass

        try:
            done = False
            for data in eacc.Scalars("train/done"):
                if data.value == 1:
                    done = True
            stats["done"] = done
        except:
            pass

        launched = datetime.datetime.timestamp(engine.launched)
        now = datetime.datetime.timestamp(datetime.datetime.now())
        power = engine.power if engine.power else 0
        power_reference = PowerUtils.get_reference_text(power, now - launched)
        power_wh = power * ((now - launched) / 3600)

        return jsonify({ "stopped": engine.has_stopped(), "stats": stats, "power": int(power_wh), "power_reference": power_reference })
    else:
        return jsonify({ "stats": [], "stopped": False })

@train_blueprint.route('/train_stats', methods=["POST"])
@utils.condec(login_required, user_utils.isUserLoginEnabled())
def train_stats():
    engine_id = request.form.get('id')
    engine = Engine.query.filter_by(id=engine_id).first()

    training_regex = r'^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d+\s+Epoch\s+(\d+)\sStep:\s+(\d+)\s+Batch Loss:\s+(\d+.\d+)\s+Tokens per Sec:\s+(\d+),\s+Lr:\s+(\d+.\d+)$'
    validation_regex = r'^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2},\d+)\s(\w|\s|\(|\))+(\d+),\s+step\s*(\d+):\s+bleu:\s+(\d+\.\d+),\s+loss:\s+(\d+\.\d+),\s+ppl:\s+(\d+\.\d+),\s+duration:\s+(\d+.\d+)s$'
    re_flags = re.IGNORECASE | re.UNICODE

    score = 0.0
    tps = []
    with open(os.path.join(engine.path, "model/train.log"), 'r') as log_file:
        for line in log_file:
            groups = re.search(training_regex, line, flags=re_flags)
            if groups:
                tps.append(float(groups[6]))
            else:
                # It was not a training line, could be validation
                groups = re.search(validation_regex, line, flags=re_flags)
                if groups:
                    bleu_score = float(groups[6])
                    score = bleu_score if bleu_score > score else score

    if len(tps) > 0:
        tps_value = reduce(lambda a, b: a + b, tps)
        tps_value = round(tps_value / len(tps))
    else:
        tps_value = "—"
    
    time_elapsed = None
    if engine.launched and engine.finished:
        launched = datetime.datetime.timestamp(engine.launched)
        finished = datetime.datetime.timestamp(engine.finished) if engine.finished else None
        time_elapsed = (finished - launched) if engine.finished else None # seconds

        if time_elapsed:
            time_elapsed_format = utils.seconds_to_timestring(time_elapsed)
        else:
            time_elapsed_format = "—"
    else:
        time_elapsed_format = "—"

    val_freq = None
    config_file_path = os.path.join(engine.path, 'config.yaml')
    with open(config_file_path, 'r') as config_file:
        config = yaml.load(config_file, Loader=yaml.FullLoader)
        val_freq = config["training"]["validation_freq"]

    vocab_size = utils.file_length(os.path.join(engine.path, 'train.vocab'))

    return jsonify({
        "result": 200, 
        "data": {
            "time_elapsed": time_elapsed_format,
            "tps": tps_value,
            "score": score,
            "validation_freq": val_freq,
            "vocab_size": vocab_size
        }
    })

@train_blueprint.route('/log', methods=["POST"])
@utils.condec(login_required, user_utils.isUserLoginEnabled())
def train_log():
    engine_id = request.form.get('engine_id')
    draw = request.form.get('draw')
    search = request.form.get('search[value]')
    start = int(request.form.get('start'))
    length = int(request.form.get('length'))
    order = int(request.form.get('order[0][column]'))
    dir = request.form.get('order[0][dir]')

    engine = Engine.query.filter_by(id = engine_id).first()
    train_log_path = os.path.join(engine.path, 'model/train.log')

    rows = []
    try:
        with open(train_log_path, 'r') as train_log:
            training_regex = r'^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d+\s+Epoch\s+(\d+)\sStep:\s+(\d+)\s+Batch Loss:\s+(\d+.\d+)\s+Tokens per Sec:\s+(\d+),\s+Lr:\s+(\d+.\d+)$'
            re_flags = re.IGNORECASE | re.UNICODE
            for line in train_log:
                groups = re.search(training_regex, line.strip(), flags=re_flags)
                if groups:
                    date_string = groups[1]
                    time_string = groups[2]
                    epoch, step = groups[3], groups[4]
                    batch_loss, tps, lr = groups[5], groups[6], groups[7]

                    # date = datetime.datetime.strptime(date_string, "%Y-%m-%d %H:%M:%S")
                    rows.append([time_string, epoch, step, batch_loss, tps, lr])
    except:
        pass

    if order is not None:
        rows.sort(key=lambda row: row[order], reverse=(dir == "desc"))

    final_rows = rows

    if start is not None and length is not None:
        final_rows = rows[start:(start + length)]

    rows_filtered = []
    if search:
        for row in final_rows:
            found = False

            for col in row:
                if not found:
                    if search in col:
                        rows_filtered.append(row)
                        found = True

    return jsonify({
        "draw": int(draw) + 1,
        "recordsTotal": len(rows),
        "recordsFiltered": len(rows_filtered) if search else len(rows),
        "data": rows_filtered if search else final_rows
    })


@train_blueprint.route('/attention/<id>')
@utils.condec(login_required, user_utils.isUserLoginEnabled())
def train_attention(id):
    if user_utils.is_normal(): return send_file(os.path.join(app.config['BASE_CONFIG_FOLDER'], "attention.png"))

    engine = Engine.query.filter_by(id = id).first()
    files = glob.glob(os.path.join(engine.path, "*.att"))
    if len(files) > 0:
        return send_file(files[0])
    else:
        return send_file(os.path.join(app.config['BASE_CONFIG_FOLDER'], "attention.png"))


def _train_stop(id, user_stop):
    Trainer.stop(id, user_stop=user_stop)
    return redirect(url_for('train.train_console', id=id))

@train_blueprint.route('/stop/<id>')
@utils.condec(login_required, user_utils.isUserLoginEnabled())
def train_stop(id):
    if user_utils.is_normal(): return redirect(url_for('index'))
        
    return _train_stop(id, True)

@train_blueprint.route('/finish/<id>')
@utils.condec(login_required, user_utils.isUserLoginEnabled())
def train_finish(id):
    if user_utils.is_normal(): return redirect(url_for('index'))
        
    return _train_stop(id, False)