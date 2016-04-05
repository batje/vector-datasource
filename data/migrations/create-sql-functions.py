from collections import namedtuple
from jinja2 import Environment
from jinja2 import FileSystemLoader
from TileStache.Goodies.VecTiles.transform import _parse_kt
import os.path
import csv
import yaml

Rule = namedtuple(
    'Rule',
    'calc equals not_equals not_exists set_memberships exists default_rule'
)


Key = namedtuple(
    'Key',
    'table key typ'
)


def _parse_kt_table(kt):
    table = None

    if kt.startswith("osm:"):
        table = "osm"
        kt = kt[4:]

    elif kt.startswith("ne:"):
        table = "ne"
        kt = kt[3:]

    elif kt.startswith("shp:"):
        table = "shp"
        kt = kt[4:]

    key, typ = _parse_kt(kt)

    return Key(table=table, key=key, typ=typ)


def format_key(kt):
    key = kt.key
    if key.startswith('tags->'):
        key = 'tags->\'%s\'' % (key[len('tags->'):])
    else:
        key = '"%s"' % key
    return key


SQL_TYPE_LOOKUP = {
    float: "real",
    int: "integer",
    str: "text"
}


def column_for_key(kt):
    key = kt.key
    if key.startswith('tags->'):
        return Key(table=kt.table, key="tags", typ="hstore")
    sql_type = SQL_TYPE_LOOKUP[kt.typ]
    return Key(table=kt.table, key=('"%s"' % key), typ=sql_type)


def format_string_value(value):
    formatted_value = "'%s'" % value
    return formatted_value


def format_calc_value(value):
    if value.startswith('${') and value.endswith('}'):
        calc_value = value[2:-1]
    else:
        # calc_value = format_string_value(value)
        calc_value = value
    return calc_value


def create_rule(keys, row, calc):
    equals = []
    not_equals = []
    not_exists = []
    set_memberships = []
    exists = []
    default_rule = None
    for key, matcher in zip(keys, row):
        assert matcher, 'Invalid value for row: %s' % row
        # skip all * values
        if matcher == '*':
            continue
        # if the matcher is surrounded by quotes, we assume that
        # should be an equals value matcher with what is inside the
        # quotes. This allows us to handle values that have a ;
        # character in them
        elif matcher.startswith('"') and matcher.endswith('"'):
            equals.append((key, matcher[1:-1]))
        elif matcher == '-':
            not_exists.append(key)
        elif matcher == '+':
            exists.append(key)
        elif matcher.startswith('-'):
            not_equals.append((key, matcher[1:]))
        elif ';' in matcher:
            candidates = matcher.split(';')
            set_memberships.append((key, candidates))
        else:
            equals.append((key, matcher))
    if not (equals or not_equals or not_exists or set_memberships or exists):
        default_rule = calc
    return Rule(
        calc, equals, not_equals, not_exists, set_memberships, exists,
        default_rule)


def create_case_statement(rules):
    when_parts = []
    default_rule = None
    for rule in rules:
        when_equals = ''
        when_not_equals = ''
        when_not_exists = ''
        when_in = ''

        when_conds = []

        # TODO consider splitting this up into separate functions to
        # more easily test
        if rule.equals:
            when_equals_parts = ['%s = %s' % (format_key(key),
                                              format_string_value(matcher))
                                 for key, matcher in rule.equals]
            when_equals = ' AND '.join(when_equals_parts)
            when_conds.append(when_equals)

        if rule.not_equals:
            when_not_equals_parts = ['%s <> %s' % (format_key(key),
                                                   format_string_value(
                                                       matcher))
                                     for key, matcher in rule.not_equals]
            when_not_equals = ' AND '.join(when_not_equals_parts)
            when_conds.append(when_not_equals)

        if rule.not_exists:
            when_not_exists_parts = ['%s IS NULL' % format_key(key)
                                     for key in rule.not_exists]
            when_not_exists = ' AND '.join(when_not_exists_parts)
            when_conds.append(when_not_exists)

        if rule.set_memberships:
            when_in_parts = []
            for key, candidates in rule.set_memberships:
                formatted_key = format_key(key)
                formatted_candidates = map(format_string_value, candidates)
                formatted_candidates = ', '.join(formatted_candidates)
                when_in_part = '%s IN (%s)' % (formatted_key,
                                               formatted_candidates)
                when_in_parts.append(when_in_part)

            when_in = ' AND '.join(when_in_parts)
            when_conds.append(when_in)

        if rule.exists:
            when_exists_parts = ['%s IS NOT NULL' % format_key(key)
                                 for key in rule.exists]
            when_exists = ' AND '.join(when_exists_parts)
            when_conds.append(when_exists)

        if rule.default_rule:
            assert not default_rule, 'Multiple default rules detected'
            # indent
            default_rule = '    ELSE %s' % rule.default_rule
            continue

        when_cond = ' AND '.join(when_conds)
        when_part = 'WHEN %s THEN %s' % (when_cond, rule.calc)
        # indent
        when_part = '    %s' % when_part
        when_parts.append(when_part)

    if default_rule:
        when_parts.append(default_rule)
    when_block = '\n'.join(when_parts)

    case_statement = 'CASE\n%s\n  END' % when_block
    return case_statement


def create_yaml_from_rules(kind_rules, min_zoom_rules):
    yaml_data = []

    for kind_rule, min_zoom_rule in zip(kind_rules, min_zoom_rules):
        equals_filter = None
        not_equals_filter = None
        exists_filter = None
        not_exists_filter = None
        any_of_filter = None

        # NOTE: probably don't need to have these two separate sections

        # assume rule is the same
        rule = kind_rule
        if rule.equals:
            # equals_filter = dict(rule.equals)
            equals_filter = {}
            for k, v in rule.equals:
                equals_filter[k.key] = v
        if rule.not_equals:
            # not_equals_filter = dict(rule.not_equals)
            not_equals_filter = {}
            for k, v in rule.not_equals:
                not_equals_filter[k.key] = v
        if rule.exists:
            # exists_filter = dict([(x, True) for x in rule.exists])
            exists_filter = dict([(x.key, True) for x in rule.exists])
        if rule.not_exists:
            # not_exists_filter = dict([(x, False) for x in rule.not_exists])
            not_exists_filter = dict([(x.key, False) for x in rule.not_exists])
        if rule.set_memberships:
            # any_of_filter = rule.set_memberships
            any_of_filter = dict([(k.key, v) for k, v in rule.set_memberships])
        if rule.default_rule:
            assert 0, 'we got a default rule!'

        yaml_datum = {}
        yaml_filter = {}

        if not_equals_filter:
            not_datum = None
            if (equals_filter or exists_filter or not_exists_filter or
                    any_of_filter):
                yaml_filter['all'] = not_datum = {}
            else:
                not_datum = yaml_filter
            not_datum['not'] = not_equals_filter

        if equals_filter:
            yaml_filter.update(equals_filter)

        if exists_filter:
            yaml_filter.update(exists_filter)

        if not_exists_filter:
            yaml_filter.update(not_exists_filter)

        if any_of_filter:
            yaml_filter.update(any_of_filter)

        yaml_datum['filter'] = yaml_filter
        yaml_datum['output'] = dict(kind=kind_rule.calc)
        yaml_datum['min_zoom'] = min_zoom_rule.calc

        yaml_data.append(yaml_datum)

    return yaml_data


def used_params(rules):
    used = set()

    for rule in rules:
        if rule.equals:
            for key, matcher in rule.equals:
                used.add(column_for_key(key))

        if rule.not_equals:
            for key, matcher in rule.not_equals:
                used.add(column_for_key(key))

        if rule.not_exists:
            for key in rule.not_exists:
                used.add(column_for_key(key))

        if rule.set_memberships:
            for key, candidates in rule.set_memberships:
                used.add(column_for_key(key))

        if rule.exists:
            for key in rule.exists:
                used.add(column_for_key(key))

    return used


layers = {}
script_root = os.path.dirname(__file__)

for layer in ('landuse', 'pois', 'transit', 'water'):
    kind_rules = []
    min_zoom_rules = []
    csv_file = '../../spreadsheets/kind/%s.csv' % layer
    file_path = os.path.join(script_root, csv_file)
    with open(file_path) as fh:
        reader = csv.reader(fh, skipinitialspace=True)
        keys = None
        for row in reader:
            if keys is None:
                # assume the last key is the kind value
                kind = row.pop(-1)
                assert kind == 'kind'
                # assume the second to last key is the min_zoom
                # TODO this might need to be changed once we have more
                # files in play
                min_zoom = row.pop(-1)
                assert min_zoom == 'min_zoom'
                keys = []
                for key_type in row:
                    key = _parse_kt_table(key_type)
                    keys.append(key)
            else:
                # assume kind is last
                kind_calc = row.pop(-1)
                # and next is the min_zoom calculation
                min_zoom_calc = row.pop(-1)

                if min_zoom_calc and min_zoom_calc == '*':
                    import pdb; pdb.set_trace();
                    assert 0, 'need min zoom calc stub'

                if kind_calc and kind_calc == '*':
                    import pdb; pdb.set_trace();
                    assert 0, 'need kind calc stub'

                if min_zoom_calc and min_zoom_calc != '*':
                    min_zoom_calc = format_calc_value(min_zoom_calc)
                    min_zoom_rule = create_rule(keys, row, min_zoom_calc)
                    min_zoom_rules.append(min_zoom_rule)

                if kind_calc and kind_calc != '*':
                    kind_calc = format_calc_value(kind_calc)
                    kind_rule = create_rule(keys, row, kind_calc)
                    kind_rules.append(kind_rule)

    layer_yaml = create_yaml_from_rules(kind_rules, min_zoom_rules)

    with open('%s.yaml' % layer, 'w') as fp:
        yaml.dump(layer_yaml, fp)

    continue

    osm_tags = set([Key(table='osm', key='tags', typ='hstore'),
                    Key(table=None, key='tags', typ='hstore')])
    params = ((used_params(kind_rules) | used_params(min_zoom_rules))
              - osm_tags)
    # sorted params is nicer to read in the sql
    params = sorted(params)
    kind_case_statement = create_case_statement(kind_rules)
    min_zoom_case_statement = create_case_statement(min_zoom_rules)
    layers[layer] = dict(
        params=params,
        kind_case_statement=kind_case_statement,
        min_zoom_case_statement=min_zoom_case_statement,
    )

import sys
sys.exit(0)

template_name = os.path.join(script_root, 'sql.jinja2')
environment = Environment(loader=FileSystemLoader('.'))
template = environment.get_template(template_name)
sql = template.render(
    layers=layers,
)
print sql
