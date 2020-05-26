from app.models import Engine
from app import db, app
from app.utils.power import PowerUtils
from app.utils import tasks
from celery.task.control import revoke

import datetime
import logging
import sys
import os
import subprocess
import pynvml
import threading

class Trainer(object):
    running_joey = {}

    @staticmethod
    def launch(user_id, id):
        task = tasks.train_engine.apply_async(args=[id])
        monitor_task = tasks.monitor_training.apply_async(args=[id])
        return task.id, monitor_task.id

    @staticmethod
    def finish(user_id, engine):
        if engine.bg_task_id:
            revoke(engine.bg_task_id, terminate=True)
            engine.bg_task_id = None
        
        if engine.pid:
            executioner = subprocess.Popen("kill -9 {}".format(engine.pid), shell=True)
            engine.pid = None
            db.session.commit()

    @staticmethod
    def stop(user_id, id, user_stop=False):
        engine = Engine.query.filter_by(id = id).first()
        Trainer.finish(user_id, engine)

        engine.status = "stopped" if user_stop else "finished"
        engine.finished = datetime.datetime.utcnow().replace(tzinfo=None)
        db.session.commit()
