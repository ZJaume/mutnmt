from app import app
from app.utils import user_utils
from app.utils.tokenizer import Tokenizer
from app.models import Engine
from toolwrapper import ToolWrapper
from lxml import etree
from nltk.tokenize import sent_tokenize
from sqlalchemy import inspect as sa_inspect
# from bs4 import BeautifulSoup, Doctype

import zipfile
import os
import re
import shutil
import glob
import subprocess

import sys

class TranslationUtils:
    def __init__(self):
        self.running_joey = {}
        self.running_users = {}

        self.format_mappings = {
            ".pptx": r'.*(slide(s*))$',
            ".docx": r'.*(document.xml)$',
            ".xlsx": r'.*sharedStrings\.xml$',
            ".libreoffice": r'.*content\.xml$'
        }
        
        self.format_filters = {
            ".pdf": "odg",
            ".rtf": "docx"
        }

        self.sentences = {}

    def reload_engine(self, id):
        if id in self.running_joey.keys():
            engine = self.running_joey[id]['engine']
            if int(engine.id) == int(id):
                if sa_inspect(engine).detached:
                    self.running_joey[id]['engine'] = Engine.query.filter_by(id = id).first()


    def launch(self, user_id, id, inspect = False):
        if user_id in self.running_users:
            if self.running_joey[self.running_users[user_id]].engine.id != id:
                self.deattach(user_id)
            else:
                return True

        if id in self.running_joey.keys():
            self.running_joey[id]['users'].append(user_id)
            self.running_users[user_id] = id
            return True

        engine = Engine.query.filter_by(id = id).first()
        joey_params = ["python3", "-m", "joeynmt", "translate", os.path.join(engine.path, "config.yaml"), "-sm"]

        if inspect:
            joey_params.append("-n")
            joey_params.append("3")
        
        slave = ToolWrapper(joey_params,
                            cwd=app.config['JOEYNMT_FOLDER'])

        welcome = slave.readline()
        if welcome == "!:SLAVE_READY":
            self.running_joey[id] = { "slave": slave, "engine": engine, "tokenizer": Tokenizer(engine), "users": [user_id] }
            self.running_users[user_id] = id
            return True

        return False

    def get(self, user_id, text):
        if user_id in self.running_users.keys():
            engine_id = self.running_users[user_id]
            user_context = self.running_joey[engine_id]
            if not user_context['tokenizer'].loaded:
                user_context['tokenizer'].load()

            joey = user_context['slave']
            joey.writeline(user_context['tokenizer'].tokenize(text))

            translation = joey.readline()
            return user_context['tokenizer'].detokenize(translation)
        else:
            return None

    def get_inspect(self, user_id, text):
        if user_id in self.running_users.keys():
            engine_id = self.running_users[user_id]
            self.reload_engine(self.running_joey[engine_id]['engine'].id)
            user_context = self.running_joey[engine_id]

            if not user_context['tokenizer'].loaded:
                user_context['tokenizer'].load()

            joey = user_context['slave']
            joey.writeline(user_context['tokenizer'].tokenize(text))

            translation = joey.readline()
            n_best = []
            while translation != "!:SLAVE_END_NBEST":
                n_best.append(translation)
                translation = joey.readline()

            return {
                "source": user_context['engine'].source.code,
                "target": user_context['engine'].target.code,
                "preproc": n_best[0], 
                "nbest": [user_context['tokenizer'].detokenize(s) for s in n_best],
                "alignments": [],
                "postproc": user_context['tokenizer'].detokenize(n_best[0])
            }
        else:
            return None

    def deattach(self, user_id):
        if user_id in self.running_users.keys():
            engine_id = self.running_users[user_id]
            self.running_joey[engine_id]['users'].remove(user_id)
            del self.running_users[user_id]

            if len(self.running_joey[engine_id]['users']) == 0:
                self.running_joey[engine_id]['slave'].close()
                del self.running_joey[engine_id]

    def norm_extension(self, extension):
        if extension in [".ppt", ".doc", ".xls"]:
            return extension + "x"
        elif extension in [".odp", ".odt", ".ods", ".odg"]:
            return ".libreoffice"
        else:
            return extension

    def translate_txt(self, user_id, file_path, as_tmx = False):
        translated_path = '{}.translated'.format(file_path)
        with open(file_path, 'r') as source:
            with open(translated_path, 'w+') as target:
                for line in source:
                    if line.strip():
                        translation = self.get(user_id, line.strip())
                        if as_tmx: self.sentences[str(user_id)].append({ "source": line.strip(), "target": [translation] })
                        print(translation, file=target)
        
        os.remove(file_path)
        shutil.move(translated_path, file_path)

    def translate_xml(self, user_id, xml_path, mode = "xml", as_tmx = False):
        def explore_node(node):
            if node.text and node.text.strip():
                translation = self.get(user_id, node.text)
                if as_tmx: self.sentences[str(user_id)].append({ "source": node.text, "target": [translation] })
                node.text = translation
            for child in node:
                explore_node(child)
        
        with open(xml_path, 'r') as xml_file:
            parser = etree.HTMLParser() if mode == "html" else etree.XMLParser()
            tree = etree.parse(xml_file, parser)
            explore_node(tree.getroot())

        tree.write(xml_path, encoding="UTF-8", xml_declaration=(mode == "xml"))

    def translate_tmx(self, user_id, tmx_path, tmx_mode):
        sentences = []

        with open(tmx_path, 'r') as xml_file:
            tmx = etree.parse(xml_file, etree.XMLParser())
            body = tmx.getroot().find("body")
            for tu in body:
                sentence = None

                for i, tuv in enumerate(tu):
                    text = tuv.find("seg").text
                    if i == 0:
                        sentence = { "source": text, "target": [] }
                    else:
                        if tmx_mode == "create":
                            sentence.get('target').append(text)
                        sentence.get('target').append(self.get(user_id, text))

                sentences.append(sentence)
            
        tmx_path_translated = self.tmx_builder(user_id, sentences)
        shutil.move(tmx_path_translated, tmx_path)

    def translate_office(self, user_id, file_path, as_tmx = False):
        filename, extension = os.path.splitext(file_path)
        norm_extension = self.norm_extension(extension)

        if norm_extension in self.format_mappings.keys():
            extract_path = '{}-extract'.format(filename)
            os.mkdir(extract_path)

            with zipfile.ZipFile(file_path, 'r') as zip:
                zip.extractall(extract_path)

            os.remove(file_path)

            for xml_file_path in [f for f in glob.glob(os.path.join(extract_path, "**/*.xml"), recursive=True)]:
                if re.search(self.format_mappings[norm_extension], xml_file_path):
                    self.translate_xml(user_id, xml_file_path, "xml", as_tmx)
            
            shutil.make_archive(filename, 'zip', extract_path, '.')
            shutil.move('{}.zip'.format(filename), file_path)
            shutil.rmtree(extract_path)

    def translate_bridge(self, user_id, file_path, original_extension, as_tmx = False):
        filename, extension = os.path.splitext(file_path)
        dest_path = filename + "." + self.format_filters[original_extension]

        convert = subprocess.Popen("soffice --convert-to {} {} --outdir {}".format(self.format_filters[original_extension],
                        file_path, os.path.dirname(dest_path)), shell=True, cwd=app.config['MUTNMT_FOLDER'], 
                        stdout=subprocess.PIPE) 
        convert.wait()

        self.translate_office(user_id, dest_path, as_tmx)

        convert = subprocess.Popen("soffice --convert-to {} {} --outdir {}".format(original_extension[1:], dest_path,
                                os.path.dirname(dest_path)), shell=True, cwd=app.config['MUTNMT_FOLDER'], stdout=subprocess.PIPE)
        convert.wait()

        os.remove(dest_path)

    def tmx_builder(self, user_id, sentences):
        engine = self.running_joey[user_id]['engine']
        source_lang = engine.source.code
        target_lang = engine.target.code

        with open(os.path.join(app.config['BASE_CONFIG_FOLDER'], 'base.tmx'), 'r') as tmx_file:
            tmx = etree.parse(tmx_file, etree.XMLParser())
            body = tmx.getroot().find("body")
            for sentence in sentences:
                tu = etree.Element("tu")

                tuv_source = etree.Element("tuv", { etree.QName("http://www.w3.org/XML/1998/namespace", "lang"): source_lang })
                seg_source = etree.Element("seg")
                seg_source.text = sentence.get('source')
                tuv_source.append(seg_source)
                tu.append(tuv_source)

                for target_sentence in sentence.get('target'):
                    tuv_target = etree.Element("tuv", { etree.QName("http://www.w3.org/XML/1998/namespace", "lang"): target_lang })
                    seg_target = etree.Element("seg")
                    seg_target.text = target_sentence
                    tuv_target.append(seg_target)
                    tu.append(tuv_target)

                body.append(tu)

        tmx_path = os.path.join('/tmp', '{}.{}-{}.tmx'.format(user_id, engine.source.code, engine.target.code))
        tmx.write(tmx_path, encoding="UTF-8", xml_declaration=True, pretty_print=True)
        return tmx_path

    def generate_tmx(self, user_id, text):
        sentences_raw = sent_tokenize(text)
        sentences = []
        for sentence in sentences_raw:
            sentences.append({ "source": sentence, "target": [self.get(user_id, sentence)] })
        return self.tmx_builder(user_id, sentences)

    def translate_file(self, user_id, file_path, as_tmx = False, tmx_mode = None):
        filename, extension = os.path.splitext(file_path)
        self.sentences[str(user_id)] = [] if as_tmx else None
        
        if extension in [".xml", ".html"]:
            self.translate_xml(user_id, file_path, extension[1:], as_tmx)
        elif extension == ".tmx":
            self.translate_tmx(user_id, file_path, tmx_mode)
        elif extension == ".txt":
            self.translate_txt(user_id, file_path, as_tmx)
        elif extension in [".rtf", ".pdf"]:
            self.translate_bridge(user_id, file_path, extension, as_tmx)
        else:
            self.translate_office(user_id, file_path, as_tmx)

        engine = self.running_joey[user_id]['engine']
        file_path_translated = '{}.{}-{}{}'.format(filename, engine.source.code, engine.target.code, extension)
        shutil.move(file_path, file_path_translated)
        file_path = file_path_translated

        if as_tmx:
            tmx_path = self.tmx_builder(user_id, self.sentences[str(user_id)])

            bundle_path = '{}-tmx-bundle'.format(filename)
            os.mkdir(bundle_path)

            filename, extension = os.path.splitext(file_path)
            basename = os.path.basename(filename)
            shutil.move(tmx_path, os.path.join(bundle_path, '{}.tmx'.format(basename)))
            shutil.move(file_path, os.path.join(bundle_path, '{}{}'.format(basename, extension)))
            shutil.make_archive(filename, 'zip', bundle_path, '.')
            shutil.rmtree(bundle_path)
