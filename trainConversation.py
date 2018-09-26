#! /usr/bin/python
# coding: utf-8

# Copyright 2018 IBM All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

""" Train assistant instance with intents and entities
    Intent input schema (headerless):
    | utterance | intent |
    Entity input schema (headerless):
    | entity | value | synonym/pattern 0 | synonym/pattern 1 | ...
"""
import json
from time import sleep
import csv
import pandas as pd
from argparse import ArgumentParser
from watson_developer_cloud import AssistantV1

from utils import UTF_8, WCS_VERSION, \
                  UTTERANCE_COLUMN, INTENT_COLUMN, \
                  TIME_TO_WAIT, WORKSPACE_ID_TAG, BASE_URL

ENTITY_COLUMN = 'entity'
ENTITY_VALUE_COLUMN = 'value'
EXAMPLES_COLUMN = 'examples'
ENTITY_VALUES_ARR_COLUMN = 'values'
SLEEP_INCRE = 10
INTENT_CSV_HEADER = [UTTERANCE_COLUMN, INTENT_COLUMN]
ENTITY_CSV_HEADER = [ENTITY_COLUMN, ENTITY_VALUE_COLUMN]


class TrainTimeoutException(Exception):
    """ To be thrown if training is timeout
    """
    def __init__(self, message):
        self.message = message


def to_examples(intent_group):
    """ Parse each row of intent group into a CreateIntent[]
    """
    res = []
    for _, row in intent_group.iterrows():
        if row['utterance']:  # Ignore empty examples
            res.append({'text': row['utterance']})

    return res


def to_entity_values(entity_group):
    """ Parse current entity group content into a CreateEntity[]
    """
    values = []
    for _, row in entity_group.iterrows():
        value = row[ENTITY_VALUE_COLUMN]
        if not value:  # Handle reserved entities
            continue

        synonyms = []
        patterns = []
        # Drop first two item and iterate the rest items (synonym or pattern)
        for _, val in row.drop([ENTITY_COLUMN, ENTITY_VALUE_COLUMN]) \
                .iteritems():
            if not pd.isnull(val):
                if val.startswith('/'):  # is pattern?
                    patterns.append(val[:-1][1:])
                else:
                    synonyms.append(val)
        # Construct CreateValue[]
        if len(patterns) != 0:
            values.append({'value': value, 'patterns': patterns,
                           'type': 'patterns'})
        else:
            values.append({'value': value, 'synonyms': synonyms,
                           'type': 'synonyms'})

    return values


def func(args):
    entities = []
    workspace_name = ''
    workspace_description = ''
    intents = []
    language = 'en'
    dialog_nodes = []
    counterexamples = []
    metadata = {}
    learning_opt_out = False

    if args.workspace_base_json is not None:
        with open(args.workspace_base_json, 'r') as f:
            workspace_json = json.load(f)
            if 'entities' in workspace_json:
                entities = workspace_json['entities']
            if 'intents' in workspace_json:
                intents = workspace_json['intents']
            if 'language' in workspace_json:
                language = workspace_json['language']
            if 'dialog_nodes' in workspace_json:
                dialog_nodes = workspace_json['dialog_nodes']
            if 'counterexamples' in workspace_json:
                counterexamples = workspace_json['counterexamples']
            if 'metadata' in workspace_json:
                metadata = workspace_json['metadata']
            if 'learning_opt_out' in workspace_json:
                learning_opt_out = workspace_json['learning_opt_out']

    if args.intentfile is not None:
        # First, group utterances by INTENT_COLUMN. In each intent group,
        # construct the CreateIntent[] and return as a cell of the series.
        # Convert the series into dataframe and restore the intent column
        # from index to an explicit column.
        intent_df = pd.read_csv(args.intentfile, quoting=csv.QUOTE_ALL,
                                encoding=UTF_8, header=None,
                                names=INTENT_CSV_HEADER,
                                keep_default_na=False) \
                      .groupby(by=[INTENT_COLUMN]).apply(to_examples) \
                      .to_frame().reset_index(level=[INTENT_COLUMN]) \
                      .rename(columns={0: EXAMPLES_COLUMN})

        # Construct the CreateIntent[]
        intents = [{'intent': row[INTENT_COLUMN],
                    'examples': row[EXAMPLES_COLUMN]}
                   for _, row in intent_df.iterrows()]

    if args.entityfile is not None:
        # Read csv with unknown number of columns into dataframe
        rows = None
        with open(args.entityfile, 'r', encoding='utf-8') as f:
            reader = csv.reader(f, quoting=csv.QUOTE_ALL)
            rows = list(reader)

        entity_df = pd.DataFrame(rows)

        # Rename 1st, 2nd column to ENTITY_COLUMN, ENTITY_VALUE_COLUMN.
        # Group rows by entity name. In each entity group,
        # construct the CreateEntity[] and return as a cell of the series.
        # Convert the series into dataframe and restore
        # the intent column from index to an explicit column.
        entity_df = entity_df.rename(
                    columns={0: ENTITY_COLUMN, 1: ENTITY_VALUE_COLUMN}) \
            .groupby(by=[ENTITY_COLUMN]).apply(to_entity_values).to_frame() \
            .reset_index(level=[ENTITY_COLUMN]) \
            .rename(columns={0: ENTITY_VALUES_ARR_COLUMN})

        # Construct the CreateEntity[]
        entities = [{'entity': row[ENTITY_COLUMN],
                     'values': row[ENTITY_VALUES_ARR_COLUMN]}
                    for _, row in entity_df.iterrows()]

    conv = AssistantV1(username=args.username, password=args.password,
                       version=WCS_VERSION, url=BASE_URL)

    if args.workspace_name is not None:
        workspace_name = args.workspace_name
    if args.workspace_description is not None:
        workspace_description = args.workspace_description

    # Create workspace with provided content
    resp = conv.create_workspace(name=workspace_name, language=language,
                                 description=workspace_description,
                                 intents=intents, entities=entities,
                                 dialog_nodes=dialog_nodes,
                                 counterexamples=counterexamples,
                                 learning_opt_out=learning_opt_out,
                                 metadata=metadata).get_result()

    # Poke the training status every SLEEP_INCRE secs
    sleep_counter = 0
    while sleep_counter < TIME_TO_WAIT:
        resp = conv.get_workspace(
            workspace_id=resp[WORKSPACE_ID_TAG]).get_result()
        if resp['status'] == 'Available':
            print(json.dumps(resp, indent=4))  # double quoted valid JSON
            return
        sleep_counter += SLEEP_INCRE
        sleep(10)

    raise TrainTimeoutException('Assistant training is timeout')


def create_parser():
    parser = ArgumentParser(
        description='Train assistant instance with intents and entities')
    parser.add_argument('-i', '--intentfile', type=str,
                        help='Intent file')
    parser.add_argument('-e', '--entityfile', type=str, help='Entity file')
    parser.add_argument('-w', '--workspace_base_json', type=str,
                        help='Workspace base JSON file')
    parser.add_argument('-n', '--workspace_name', type=str,
                        help='Workspace name')
    parser.add_argument('-d', '--workspace_description', type=str,
                        help='Workspace description')
    parser.add_argument('-u', '--username', type=str, required=True,
                        help='Assistant service username')
    parser.add_argument('-p', '--password', type=str, required=True,
                        help='Assistant service password')

    return parser


if __name__ == '__main__':
    ARGS = create_parser().parse_args()
    func(ARGS)
