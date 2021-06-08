# coding: utf-8

import copy
from functools import reduce
import logging
from typing import List

from math import ceil
from queries import Query, where

import friedrich_db

from results import (
    InsertOneResult,
    InsertManyResult,
    UpdateResult,
    DeleteResult
)

from errors import DuplicateKeyError, InvalidName

try:
    basestring
except NameError:
    basestring = str

logger = logging.getLogger(__name__)


def Q(query, key):
    return reduce(lambda partial_query, field: partial_query[field], key.split('.'), query)


def _check_name(name):
    """Check if a database name is valid.
    """
    if not name:
        raise InvalidName("database name cannot be the empty string")

    for invalid_char in [' ', '.', '$', '/', '\\', '\x00', '"']:
        if invalid_char in name:
            raise InvalidName("database names cannot contain the "
                              "character %r" % invalid_char)


class Client:
    def __init__(self):
        self.client = friedrich_db.Client()
        print("init Client")

    def __getitem__(self, key):
        return DataBase(self.client, key)

    def close(self):
        pass

    def __getattr__(self, name):
        return DataBase(self.client, name)


class DataBase:
    def __init__(self, client: Client, name: str):
        self.client: Client = client
        self.name: str = name
        self.database = self.client[self.name]
        print("init Database")

    def __getattr__(self, name):
        print("__getattr__ 0")
        return Collection(name, self.database)

    def __getitem__(self, name):
        print("__getattr__ 1")
        return Collection(name, self.database)

    def collection_names(self) -> List[str]:
        return self.database.collection_name()


class Collection:
    def __init__(self, name, database):
        self.name = name
        self.database = database
        self.collection = None
        print("init Collection")

    def __len__(self):
        return len(self.collection)

    def __repr__(self):
        return self.name

    def __getattr__(self, name):
        print("0 __getattr__ Collection")
        if self.collection is None:
            print("1 __getattr__ Collection")
            self.build_table()
        print("2 __getattr__ Collection")
        return self

    def build_table(self):
        self.collection = self.database[self.name]

    def count(self):
        return self.find().count()

    def drop(self, **kwargs):
        if self.collection:
            self.database.drop_collection(self.name)
            return True
        else:
            return False

    def insert(self, docs, *args, **kwargs):
        if isinstance(docs, list):
            return self.insert_many(docs, *args, **kwargs)
        else:
            return self.insert_one(docs, *args, **kwargs)

    def insert_one(self, doc, *args, **kwargs):
        print("1 insert_one")
        if self.collection is None:
            self.build_table()
        print("2 insert_one")
        if not isinstance(doc, dict):
            raise ValueError(u'"doc" must be a dict')
        print("3 insert_one")
        _id = doc[u'_id'] = doc.get('_id') or friedrich_db.generate_id()
        print("4 insert_one")
        bypass_document_validation = kwargs.get('bypass_document_validation')
        if bypass_document_validation is True:
            # insert doc without validation of duplicated `_id`
            print("5 insert_one")
            eid = self.collection.insert(doc)
        else:
            print("6 insert_one")
            existing = self.find_one({'_id': _id})
            print(existing)
            print("7 insert_one")
            if existing is None:
                print("8 insert_one")
                eid = self.collection.insert(doc)
                print(eid)
                print("9 insert_one")
            else:
                raise DuplicateKeyError(
                    u'_id:{0} already exists in collection:{1}'.format(
                        _id, self.name
                    )
                )

        return InsertOneResult(eid=eid, inserted_id=_id)

    def insert_many(self, docs, *args, **kwargs):
        if self.collection is None:
            self.build_table()

        if not isinstance(docs, list):
            raise ValueError(u'"insert_many" requires a list input')

        bypass_document_validation = kwargs.get('bypass_document_validation')

        if bypass_document_validation is not True:
            # get all _id in once, to reduce I/O. (without projection)
            existing = [doc['_id'] for doc in self.find({})]

        _ids = list()
        for doc in docs:

            _id = doc[u'_id'] = doc.get('_id') or generate_id()

            if bypass_document_validation is not True:
                if _id in existing:
                    raise DuplicateKeyError(
                        u'_id:{0} already exists in collection:{1}'.format(
                            _id, self.name
                        )
                    )
                existing.append(_id)

            _ids.append(_id)

        results = self.collection.insert_multiple(docs)

        return InsertManyResult(
            eids=[eid for eid in results],
            inserted_ids=[inserted_id for inserted_id in _ids]
        )

    def parse_query(self, query):
        logger.debug(u'query to parse2: {}'.format(query))

        # this should find all records
        if query == {} or query is None:
            return Query()._id != u'-1'  # noqa

        q = None
        # find the final result of the generator
        for c in self.parse_condition(query):
            if q is None:
                q = c
            else:
                q = q & c

        logger.debug(u'new query item2: {}'.format(q))

        return q

    def parse_condition(self, query, prev_key=None, last_prev_key=None):
        # use this to determine gt/lt/eq on prev_query
        logger.debug(u'query: {} prev_query: {}'.format(query, prev_key))

        q = Query()
        conditions = None

        # deal with the {'name': value} case by injecting a previous key
        if not prev_key:
            temp_query = copy.deepcopy(query)
            k, v = temp_query.popitem()
            prev_key = k

        # deal with the conditions
        for key, value in query.items():
            logger.debug(u'conditions: {} {}'.format(key, value))

            if key == u'$gte':
                conditions = (
                        Q(q, prev_key) >= value
                ) if not conditions and prev_key != "$not" \
                    else (conditions & (Q(q, prev_key) >= value)) if prev_key != "$not" \
                    else (q[last_prev_key] < value)
            elif key == u'$gt':
                conditions = (
                        Q(q, prev_key) > value
                ) if not conditions and prev_key != "$not" \
                    else (conditions & (Q(q, prev_key) > value)) if prev_key != "$not" \
                    else (q[last_prev_key] <= value)
            elif key == u'$lte':
                conditions = (
                        Q(q, prev_key) <= value
                ) if not conditions and prev_key != "$not" \
                    else (conditions & (Q(q, prev_key) <= value)) if prev_key != "$not" \
                    else (q[last_prev_key] > value)
            elif key == u'$lt':
                conditions = (
                        Q(q, prev_key) < value
                ) if not conditions and prev_key != "$not" \
                    else (conditions & (Q(q, prev_key) < value)) if prev_key != "$not" \
                    else (q[last_prev_key] >= value)
            elif key == u'$ne':
                conditions = (
                        Q(q, prev_key) != value
                ) if not conditions and prev_key != "$not" \
                    else (conditions & (Q(q, prev_key) != value)) if prev_key != "$not" \
                    else (q[last_prev_key] == value)
            elif key == u'$not':
                if not isinstance(value, dict) and not isinstance(value, list):
                    conditions = (
                            Q(q, prev_key) != value
                    ) if not conditions and prev_key != "$not" \
                        else (conditions & (Q(q, prev_key) != value)) \
                        if prev_key != "$not" else (q[last_prev_key] >= value)
                else:
                    # let the value's condition be parsed below
                    pass
            elif key == u'$regex':
                value = value.replace('\\\\\\', '|||')
                value = value.replace('\\\\', '|||')
                regex = value.replace('\\', '')
                regex = regex.replace('|||', '\\')
                currCond = (where(prev_key).matches(regex))
                conditions = currCond if not conditions else (conditions & currCond)
            elif key in ['$and', '$or', '$in', '$all']:
                pass
            else:

                # don't want to use the previous key if this is a secondary key
                # (fixes multiple item query that includes $ codes)
                if not isinstance(value, dict) and not isinstance(value, list):
                    conditions = (
                            (Q(q, key) == value) | (Q(q, key).any([value]))
                    ) if not conditions else (conditions & ((Q(q, key) == value) | (Q(q, key).any([value]))))
                    prev_key = key

            logger.debug(u'c: {}'.format(conditions))
            if isinstance(value, dict):
                # yield from self.parse_condition(value, key)
                for parse_condition in self.parse_condition(value, key, prev_key):
                    yield parse_condition
            elif isinstance(value, list):
                if key == '$and':
                    grouped_conditions = None
                    for spec in value:
                        for parse_condition in self.parse_condition(spec):
                            grouped_conditions = (
                                parse_condition
                                if not grouped_conditions
                                else grouped_conditions & parse_condition
                            )
                    yield grouped_conditions
                elif key == '$or':
                    grouped_conditions = None
                    for spec in value:
                        for parse_condition in self.parse_condition(spec):
                            grouped_conditions = (
                                parse_condition
                                if not grouped_conditions
                                else grouped_conditions | parse_condition
                            )
                    yield grouped_conditions
                elif key == '$in':
                    # use `any` to find with list, before comparing to single string
                    grouped_conditions = Q(q, prev_key).any(value)
                    for val in value:
                        for parse_condition in self.parse_condition({prev_key: val}):
                            grouped_conditions = (
                                parse_condition
                                if not grouped_conditions
                                else grouped_conditions | parse_condition
                            )
                    yield grouped_conditions
                elif key == '$all':
                    yield Q(q, prev_key).all(value)
                else:
                    yield Q(q, prev_key).any([value])
            else:
                yield conditions

    def update(self, query, doc, *args, **kwargs):
        if isinstance(doc, list):
            return [
                self.update_one(query, item, *args, **kwargs)
                for item in doc
            ]
        else:
            return self.update_one(query, doc, *args, **kwargs)

    def update_one(self, query, doc):
        if self.collection is None:
            self.build_table()

        if u"$set" in doc:
            doc = doc[u"$set"]

        allcond = self.parse_query(query)

        try:
            result = self.collection.update(doc, allcond)
        except:
            # TODO: check table.update result
            # check what pymongo does in that case
            result = None

        return UpdateResult(raw_result=result)

    def find(self, filter=None, sort=None, skip=None, limit=None, *args, **kwargs):
        print(1)
        if self.collection is None:
            self.build_table()
        print(2)
        if filter is None:
            print(3)
            print(dir(self.collection))
            print(hasattr(self.collection,"all"))
            result = self.collection.all()
            print(4)
        else:
            print(4)
            print(filter)
            allcond = self.parse_query(filter)
            print(5)
            print(type(self.collection))
            print(dir(self.collection))
            print(hasattr(self.collection,"search"))
            result = self.collection.search(allcond)
            print(result)
        print(5)
        result = Cursor(
            result,
            sort=sort,
            skip=skip,
            limit=limit
        )

        return result

    def find_one(self, filter=None):
        print("0 find_one")
        if self.collection is None:
            print("1 find_one")
            self.build_table()
        print("2 find_one")
        allcond = self.parse_query(filter)
        print("3 find_one")
        print(filter)
        print(type(self.collection))
        print(allcond)
        print("4 find_one")
        return self.collection.get(allcond)

    def remove(self, spec_or_id, multi=True, *args, **kwargs):
        if multi:
            return self.delete_many(spec_or_id)
        return self.delete_one(spec_or_id)

    def delete_one(self, query):
        item = self.find_one(query)
        result = self.collection.remove(where(u'_id') == item[u'_id'])

        return DeleteResult(raw_result=result)

    def delete_many(self, query):
        items = self.find(query)
        result = [
            self.collection.remove(where(u'_id') == item[u'_id'])
            for item in items
        ]

        if query == {}:
            self.collection.drop()

        return DeleteResult(raw_result=result)


class Cursor:
    def __init__(self, cursordat, sort=None, skip=None, limit=None):
        self.cursordat = cursordat
        self.cursorpos = -1

        if len(self.cursordat) == 0:
            self.currentrec = None
        else:
            self.currentrec = self.cursordat[self.cursorpos]

        if sort:
            self.sort(sort)

        self.paginate(skip, limit)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.cursordat[key]
        return self.currentrec[key]

    def paginate(self, skip, limit):
        if not self.count() or not limit:
            return
        skip = skip or 0
        pages = int(ceil(self.count() / float(limit)))
        limits = {}
        last = 0
        for i in range(pages):
            current = limit * i
            limits[last] = current
            last = current
        # example with count == 62
        # {0: 20, 20: 40, 40: 60, 60: 62}
        if limit and limit < self.count():
            limit = limits.get(skip, self.count())
            self.cursordat = self.cursordat[skip: limit]

    def _order(self, value, is_reverse=None):
        def _dict_parser(dict_doc):
            result = list()
            for key in dict_doc:
                data = self._order(dict_doc[key])
                res = (data[0], key, data[1])
                result.append(res)
            return tuple(result)

        def _list_parser(list_doc):
            result = list()
            for member in list_doc:
                result.append(self._order(member))
            return result

        # (TODO) include more data type
        if value is None or not isinstance(value, (dict,
                                                   list,
                                                   basestring,
                                                   bool,
                                                   float,
                                                   int)):
            # not support/sortable value type
            value = (0, None)

        elif isinstance(value, bool):
            value = (5, value)

        elif isinstance(value, (int, float)):
            value = (1, value)

        elif isinstance(value, basestring):
            value = (2, value)

        elif isinstance(value, dict):
            value = (3, _dict_parser(value))

        elif isinstance(value, list):
            if len(value) == 0:
                # [] less then None
                value = [(-1, [])]
            else:
                value = _list_parser(value)

            if is_reverse is not None:
                # list will firstly compare with other doc by it's smallest
                # or largest member
                value = max(value) if is_reverse else min(value)
            else:
                # if the smallest or largest member is a list
                # then compaer with it's sub-member in list index order
                value = (4, tuple(value))

        return value

    def sort(self, key_or_list, direction=None):
        # checking input format

        sort_specifier = list()
        if isinstance(key_or_list, list):
            if direction is not None:
                raise ValueError('direction can not be set separately '
                                 'if sorting by multiple fields.')
            for pair in key_or_list:
                if not (isinstance(pair, list) or isinstance(pair, tuple)):
                    raise TypeError('key pair should be a list or tuple.')
                if not len(pair) == 2:
                    raise ValueError('Need to be (key, direction) pair')
                if not isinstance(pair[0], basestring):
                    raise TypeError('first item in each key pair must '
                                    'be a string')
                if not isinstance(pair[1], int) or not abs(pair[1]) == 1:
                    raise TypeError('bad sort specification.')

            sort_specifier = key_or_list

        elif isinstance(key_or_list, basestring):
            if direction is not None:
                if not isinstance(direction, int) or not abs(direction) == 1:
                    raise TypeError('bad sort specification.')
            else:
                # default ASCENDING
                direction = 1

            sort_specifier = [(key_or_list, direction)]

        else:
            raise ValueError('Wrong input, pass a field name and a direction,'
                             ' or pass a list of (key, direction) pairs.')

        # sorting

        _cursordat = self.cursordat

        total = len(_cursordat)
        pre_sect_stack = list()
        for pair in sort_specifier:

            is_reverse = bool(1 - pair[1])
            value_stack = list()
            for index, data in enumerate(_cursordat):

                # get field value

                not_found = None
                for key in pair[0].split('.'):
                    not_found = True

                    if isinstance(data, dict) and key in data:
                        data = copy.deepcopy(data[key])
                        not_found = False

                    elif isinstance(data, list):
                        if not is_reverse and len(data) == 1:
                            # MongoDB treat [{data}] as {data}
                            # when finding fields
                            if isinstance(data[0], dict) and key in data[0]:
                                data = copy.deepcopy(data[0][key])
                                not_found = False

                        elif is_reverse:
                            # MongoDB will keep finding field in reverse mode
                            for _d in data:
                                if isinstance(_d, dict) and key in _d:
                                    data = copy.deepcopy(_d[key])
                                    not_found = False
                                    break

                    if not_found:
                        break

                # parsing data for sorting

                if not_found:
                    # treat no match as None
                    data = None

                value = self._order(data, is_reverse)

                # read previous section
                pre_sect = pre_sect_stack[index] if pre_sect_stack else 0
                # inverse if in reverse mode
                # for keeping order as ASCENDING after sort
                pre_sect = (total - pre_sect) if is_reverse else pre_sect
                _ind = (total - index) if is_reverse else index

                value_stack.append((pre_sect, value, _ind))

            # sorting cursor data

            value_stack.sort(reverse=is_reverse)

            ordereddat = list()
            sect_stack = list()
            sect_id = -1
            last_dat = None
            for dat in value_stack:
                # restore if in reverse mode
                _ind = (total - dat[-1]) if is_reverse else dat[-1]
                ordereddat.append(_cursordat[_ind])

                # define section
                # maintain the sorting result in next level sorting
                if not dat[1] == last_dat:
                    sect_id += 1
                sect_stack.append(sect_id)
                last_dat = dat[1]

            # save result for next level sorting
            _cursordat = ordereddat
            pre_sect_stack = sect_stack

        # done

        self.cursordat = _cursordat

        return self

    def hasNext(self):
        cursor_pos = self.cursorpos + 1

        try:
            self.cursordat[cursor_pos]
            return True
        except IndexError:
            return False

    def next(self):
        self.cursorpos += 1
        return self.cursordat[self.cursorpos]

    def count(self, with_limit_and_skip=False):
        return len(self.cursordat)