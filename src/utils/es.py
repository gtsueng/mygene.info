"""Module to query ES indexes"""

#http://www.elasticsearch.org/guide/reference/query-dsl/custom-filters-score-query.html
#http://www.elasticsearch.org/guide/reference/query-dsl/custom-score-query.html
#http://www.elasticsearch.org/guide/reference/query-dsl/custom-boost-factor-query.html
#http://www.elasticsearch.org/guide/reference/query-dsl/boosting-query.html

import types
import json
import re
import time
import copy

from pyes import ES
from pyes.exceptions import NotFoundException
from pyes.utils import make_path
from pyes.query import MatchAllQuery, StringQuery

from config import ES_HOST, ES_INDEX_NAME, ES_INDEX_TYPE
from utils.common import is_int, timesofar


def get_es():
    conn = ES(ES_HOST, default_indices=[ES_INDEX_NAME],
              timeout=120.0, max_retries=10)
    return conn


es = get_es()


dummy_model = lambda es, res: res


class ESQuery:
    def __init__(self):
        #self.conn0 = es0
        self.conn = es
        self.conn.model = dummy_model
        # self._index = 'genedoc_mygene'
        # self._index = 'genedoc_mygene_allspecies'
        # self._doc_type = 'gene'
        self._index = ES_INDEX_NAME
        self._doc_type = ES_INDEX_TYPE

        #self._doc_type = 'gene_sample'
        self._default_fields = ['name', 'symbol', 'taxid', 'entrezgene', 'ensemblgene']

    def _search(self, q):
        #return self.conn0.search(q, index=self._index, doc_type=self._doc_type)
        return self.conn.search_raw(q, indices=self._index, doc_types=self._doc_type)

    def _msearch(self, q):
        path = make_path((self._index, self._doc_type, '_msearch'))
        return self.conn._send_request('GET', path, body=q)

    def _search_async(self, q, callback=None):
        import tornado.httpclient
        import tornado.ioloop
        tornado.httpclient.AsyncHTTPClient.configure("tornado.curl_httpclient.CurlAsyncHTTPClient")
        http = tornado.httpclient.AsyncHTTPClient()
        path = make_path((self._index, self._doc_type, '_search'))
        uri = ES_HOST + path
        body = json.dumps(q)
        loop = tornado.ioloop.IOLoop.instance()
        def es_query_callback(response):
            if response.error:
                print "Error:", response.error
                response.rethrow()
            else:
                print "Success"
                res = tornado.escape.json_decode(response.body)
                callback(res)
                loop.stop()
        print uri
        print body
        response = http.fetch(uri, es_query_callback, method="POST", body=body)
        loop.start()

    def _get_genedoc(self, hit):
        doc = hit.get('_source', hit.get('fields'))
        doc.setdefault('_id', hit['_id'])
        if '_version' in hit:
            doc.setdefault('_version', hit['_version'])
        return doc

    def _cleaned_res(self, res, empty=[], error={'error': True}, single_hit=False):
        '''res is the dictionary returned from a query.'''
        if 'error' in res:
            return error

        hits = res['hits']
        total = hits['total']
        if total == 0:
            return empty
        elif total == 1 and single_hit:
            return self._get_genedoc(hits['hits'][0])
        else:
            return [self._get_genedoc(hit) for hit in hits['hits']]

    def _formated_fields(self, fields):
        if type(fields) in types.StringTypes:
            fields = [x.strip() for x in fields.split(',')]
        return fields

    def _parse_interval_query(self, query):
        '''Check if the input query string matches interval search regex,
           if yes, return a dictionary with three key-value pairs:
              chr
              gstart
              gend
            , otherwise, return None.
        '''
        pattern = r'chr(?P<chr>\w+):(?P<gstart>[0-9,]+)-(?P<gend>[0-9,]+)'
        if query:
            mat = re.search(pattern, query)
            if mat:
                return mat.groupdict()

    def _is_raw_string_query(self, query):
        '''Return True if input query is a wildchar/fielded/boolean query.'''
        for v in ["*", "?", ':',' AND ', ' OR ']:
            if query.find(v) != -1:
                return True
        return False

    def get_gene(self, geneid, fields=None, **kwargs):
        if fields:
            kwargs['fields'] = self._formated_fields(fields)
        raw = kwargs.pop('raw', False)
        #res = self.conn0.get(self._index, self._doc_type, geneid, **kwargs)
        try:
            res = self.conn.get(self._index, self._doc_type, geneid, **kwargs)
        except NotFoundException:
            return None
        return res if raw else self._get_genedoc(res)

    def mget_gene(self, geneid_list, fields=None, **kwargs):
        if fields:
            kwargs['fields'] = self._formated_fields(fields)
        raw = kwargs.pop('raw', False)
        res = self.conn.mget(geneid_list, self._index, self._doc_type, **kwargs)
        return res if raw else [self._get_genedoc(doc) for doc in res]

    def get_gene2(self, geneid, fields=None, **kwargs):
        if fields:
            fields = self._formated_fields(fields)
        raw = kwargs.pop('raw', False)
        rawquery = kwargs.pop('rawquery', None)
        scopes = kwargs.pop('scopes', None)
        if scopes:
            scopes = self._formated_fields(scopes)
        qbdr = ESQueryBuilder(fields=fields, **kwargs)
        _q = qbdr.build_id_query(geneid, scopes)
        if rawquery:
            return _q
        res =  self._search(_q)
        return res if raw else self._cleaned_res(res, empty=None, single_hit=True)

    def mget_gene2(self, geneid_list, fields=None, **kwargs):
        if fields:
            fields = self._formated_fields(fields)
        raw = kwargs.pop('raw', False)
        rawquery = kwargs.pop('rawquery', None)
        scopes = kwargs.pop('scopes', None)
        if scopes:
            scopes = self._formated_fields(scopes)
        qbdr = ESQueryBuilder(fields=fields, **kwargs)
        _q = qbdr.build_multiple_id_query(geneid_list, scopes)
        if rawquery:
            return _q
        res = self._msearch(_q)
        return [_res if raw else self._cleaned_res(_res, empty=None, single_hit=True) for _res in res['responses']]

    def query(self, q, fields=['symbol','name','taxid','entrezgene', 'ensemblgene'], **kwargs):
        if fields:
            fields = self._formated_fields(fields)
        mode = int(kwargs.pop('mode', 1))
        raw = kwargs.pop('raw', False)
        rawquery = kwargs.pop('rawquery', None)
        qbdr = ESQueryBuilder(fields=fields, **kwargs)
        _q = None
        # Check if special interval query pattern exists
        interval_query = self._parse_interval_query(q)
        if interval_query:
            #should also passing a "taxid" along with interval.
            taxid = kwargs.pop('taxid', None)
            if taxid:
                interval_query['taxid'] = taxid
                _q = qbdr.build_genomic_pos_query(**interval_query)

        # Check if wildchar/fielded/boolean query, excluding special goid query
        elif self._is_raw_string_query(q) and not q.lower().startswith('go:'):
            _q = qbdr.build(q, mode=3)   #raw string query
        else:
        # normal text query
            _q = qbdr.build(q, mode)
        if _q:
            if rawquery:
                return _q

            res = self._search(_q)
            if not raw:
                _res = res['hits']
                _res['took'] = res['took']
                for v in _res['hits']:
                    del v['_type']
                    del v['_index']
                    for attr in ['fields', '_source']:
                        if attr in v:
                            v.update(v[attr])
                            del v[attr]
                            break
                res = _res
        else:
            res = {'error': "Invalid query. Please check parameters."}

        return res

    def query_sample(self, q, **kwargs):
        self._doc_type = 'gene_sample'
        res = self.query(q, **kwargs)
        self._doc_type = 'gene'
        return res

    def query_interval(self, taxid, chr,  gstart, gend, **kwargs):
        kwargs.setdefault('fields', ['symbol','name','taxid'])
        rawquery = kwargs.pop('rawquery', None)
        qbdr = ESQueryBuilder(**kwargs)
        _q = qbdr.build_genomic_pos_query(taxid, chr,  gstart, gend)
        if rawquery:
            return _q
        return self._search(_q)

    def doc_feeder(self, step=1000, s=None, e=None, inbatch=False, query=None, **kwargs):
        '''A iterator for returning docs in a ES index with batch query.
           additional filter query can be passed via "query", e.g.,
           doc_feeder(query='taxid:9606'}})
           other parameters can be passed via "**kwargs":
                fields, from, size etc.
        '''
        if query:
            q = StringQuery(query)
        else:
            q = MatchAllQuery()
        raw_res = None

        cnt = 0
        t0 = time.time()
        while 1:
            t1 = time.time()
            if raw_res is None:
                raw_res = self.conn.search_raw(q, self._index, self._doc_type,
                      start=s, size=step, scan=True, scroll='5m', **kwargs)
                n = raw_res['hits']['total']
                print 'Retrieving %d documents from index "%s/%s".' % (n, self._index, self._doc_type)
            else:
                raw_res = self.conn.search_scroll(raw_res._scroll_id, scroll='5m')
            hits_cnt = len(raw_res['hits']['hits'])
            if hits_cnt == 0:
                break
            else:

                print "Processing %d-%d documents..." % (cnt+1, cnt+hits_cnt) ,
                res = self._cleaned_res(raw_res)
                if inbatch:
                    yield res
                else:
                    for hit in res:
                        yield hit
                cnt += hits_cnt
                print 'Done.[%.1f%%,%s]' % (cnt*100./n, timesofar(t1))
                if e and cnt > e:
                    break

        print "="*20
        print 'Finished.[total docs: %s, total time: %s]' % (cnt, timesofar(t0))


    def metadata(self, raw=False):
        '''return metadata about the index.'''
        mapping = self.conn.indices.get_mapping(self._doc_type, self._index)
        if raw:
            return mapping

        def get_fields(properties):
            for k, v in properties.items():
                if 'properties' in v:
                    for f in get_fields(v['properties']):
                        yield f
                else:
                    if v.get('index', None) == 'no':
                        continue
                    f = v.get('index_name', k)
                    yield f

        field_set = set(get_fields(mapping[self._doc_type]['properties']))
        metadata = {
            'available_fields': sorted(field_set)
        }
        if '_meta' in mapping[self._doc_type]:
            metadata.update(mapping[self._doc_type]['_meta'])
        return metadata


def test2(q):
    esq = ESQuery()
    return esq.query(q)


class ESQueryBuilder():
    def __init__(self, **query_options):
        """You can pass these options:
            fields     default ['name', 'symbol', 'taxid', 'entrezgene', 'ensemblgene']
            from       default 0
            size       default 10
            sort       e.g. sort='entrezgene,-symbol'
            explain    true or false
        """
        self.options = query_options
        self._parse_sort_option(self.options)
        self._allowed_options = ['fields', 'start', 'from', 'size', 'sort', 'explain', 'version', 'species']
        for key in set(self.options) - set(self._allowed_options):
                del self.options[key]

        self._default_species = [9606, 10090, 10116, 7227, 6239]  #human, mouse, rat, fruitfly, celegan
        self.species = self.options.pop('species', self._default_species)
        #if self.species == 'all': do not apply species filter, all species is included.
        if type(self.species) is types.IntType:
            self.species = [self.species]

        #this is a fake query to make sure to return empty hits
        self._nohits_query = {
                            "match": {
                                'non_exist_field': ''
                            }
                        }

    def _parse_sort_option(self, options):
        sort = options.get('sort', None)
        if sort:
            _sort_array = []
            for field in sort.split(','):
                field = field.strip()
                if field == 'name' or field[1:]=='name':
                    #sorting on "name" field is ignored, as it is a multi-text field.
                    continue
                if field.startswith('-'):
                    _f = {"%s" % field[1:]: "desc"}
                else:
                    _f = {"%s" % field: "asc"}
                _sort_array.append(_f)
            options["sort"] = _sort_array
        return options

    def dis_max_query(self, q):
        _query = {
            "dis_max" : {
                "tie_breaker" : 0,
                "boost" : 1,
                "queries" : [
                    {
                    "custom_boost_factor": {
                        "query" : {
                            "match" : { "symbol" : {
                                            "query": "%(q)s",
                                            "analyzer": "whitespace_lowercase"
                                            }
                                      },
                        },
                        "boost_factor": 5
                    }
                    },
                    {
                    "custom_boost_factor": {
                        "query" : {
                            #This makes phrase match of "cyclin-dependent kinase 2" appears first
                            "match_phrase" : { "name" : "%(q)s"},
                        },
                        "boost_factor": 4
                    }
                    },
                    {
                    "custom_boost_factor": {
                        "query" : {
                            "match" : { "name" : {
                                            "query": "%(q)s",
                                            "analyzer": "whitespace_lowercase"
                                            }
                                      },
                        },
                        "boost_factor" : 3
                    }
                    },
                    {
                    "custom_boost_factor": {
                        "query" : {
                            "match" : { "unigene" : {
                                                    "query": "%(q)s" ,
                                                    "analyzer": "string_lowercase"
                                                 }
                                             }
                        },
                        "boost_factor": 1.1
                    }
                    },
                    {
                    "custom_boost_factor": {
                        "query" : {
                            "match" : { "go" : {
                                                    "query": "%(q)s" ,
                                                    "analyzer": "string_lowercase"
                                                 }
                                             }
                        },
                        "boost_factor": 1.1
                    }
                    },
                    {
                    "custom_boost_factor": {
                        "query" : {
                            "match" : { "_all" : {
                                            "query": "%(q)s",
                                            "analyzer": "whitespace_lowercase"
                                }
                            },
                        },
                        "boost_factor": 1
                    }
                    },

                ]
            }
            }
        _query = json.dumps(_query)
        _query = json.loads(_query % {'q': q})

        if is_int(q):
            _query['dis_max']['queries'] = []
            _query['dis_max']['queries'].insert(0,
                    {
                    "custom_boost_factor": {
                        "query" : {
                            "term" : { "entrezgene" : int(q)},
                        },
                        "boost_factor": 8
                    }
                    }
                    )


        return _query

    def string_query(self, q):
        _query = {
            "query_string": {
                "query": "%(q)s",
                "analyzer": "string_lowercase",
                "default_operator": "AND",
                "auto_generate_phrase_queries": True
            }
        }
        _query = json.dumps(_query)
        q = "symbol:%(q)s OR name:%(q)s OR %(q)s" % {'q': q}
        _query = json.loads(_query % {'q': q})
        return _query

    def raw_string_query(self, q):
        _query = {
            "query_string": {
                "query": "%(q)s",
#                "analyzer": "string_lowercase",
                "default_operator": "AND",
                "auto_generate_phrase_queries": True
            }
        }
        _query = json.dumps(_query)
        _query = json.loads(_query % {'q': q})
        return _query

    def add_species_filter(self, _query):
        if self.species == 'all':
            #do not apply species filter
            return _query

        _query = {
            'filtered': {
                'query': _query,
                'filter' : {
                    "terms" : {
                        "taxid" : self.species
                    }
                }
            }
        }
        return _query

    def add_species_custom_filters_score(self, _query):
        _query = {
            "custom_filters_score": {
            "query": _query,
            "filters" : [
                #downgrade "pseudogene" matches
                {
                    "filter" : { "term" : { "name" : "pseudogene" } },
                    "boost" : "0.5"
                },

                {
                    "filter" : { "term" : { "taxid" : 9606 } },
                    "boost" : "1.5"
                },
                {
                    "filter" : { "term" : { "taxid" : 10090 } },
                    "boost" : "1.3"
                },
                {
                    "filter" : { "term" : { "taxid" : 10116 } },
                    "boost" : "1.1"
                },

            ],
            "score_mode" : "first"
            }
        }
        return _query

    def build(self, q, mode=1):
        if mode == 1:
            _query = self.dis_max_query(q)
            print 'dis_max'
        elif mode == 2:
            _query = self.string_query(q)
            print 'string'
        else:
            _query = self.raw_string_query(q)
            print 'raw_string'

        _query = self.add_species_filter(_query)
        _query = self.add_species_custom_filters_score(_query)
        _q = {'query': _query}
        if self.options:
            _q.update(self.options)
        return _q

    def build_id_query(self, id, scopes=None):
        id_is_int = is_int(id)
        if scopes is None:
            #by default search three fields ['entrezgene', 'ensemblgene', 'retired']
            if id_is_int:
                _query = {
                    "multi_match": {
                        "query": id,
                        "fields": ['entrezgene', 'retired']
                    }
                }
            else:
                _query = {
                    "match": {
                        "ensemblgene": "%s" % id
                    }
                }
        else:
            if type(scopes) in types.StringTypes:
                _field = scopes
                if _field in ['entrezgene', 'retired']:
                    if id_is_int:
                        _query = {
                            "match": {
                                _field: id
                            }
                        }
                    else:
                        #raise ValueError('fields "%s" requires an integer id to query' % _field)
                        #using a fake query here to make sure return empty hits
                        _query = self._nohits_query
                else:
                    _query = {
                        "match": {
                            _field: "%s" % id
                        }
                    }
            elif type(scopes) in (types.ListType, types.TupleType):
                int_fields = []
                str_fields = copy.copy(scopes)
                if 'entrezgene' in str_fields:
                    int_fields.append('entrezgene')
                    str_fields.remove('entrezgene')
                if 'retired' in str_fields:
                    int_fields.append('retired')
                    str_fields.remove('retired')

                if id_is_int:
                    if len(int_fields) == 1:
                        _query = {
                            "match": {
                                int_fields[0]: id
                            }
                        }
                    elif len(int_fields) == 2:
                        _query = {
                            "multi_match": {
                                "query": id,
                                "fields": int_fields
                            }
                        }
                    else:
                        _query = self._nohits_query
                elif str_fields:
                    _query = {
                        "multi_match": {
                            "query": "%s" % id,
                            "fields": str_fields
                        }
                    }
                else:
                    _query = self._nohits_query

            else:
                raise ValueError('"scopes" cannot be "%s" type' % type(scopes))

        _query = self.add_species_filter(_query)
        _q = {"query": _query}
        if self.options:
            _q.update(self.options)
        return _q

    def build_multiple_id_query(self, id_list, scopes=None):
        """make a query body for msearch query."""
        _q = []
        for id in id_list:
            _q.extend(['{}', json.dumps(self.build_id_query(id, scopes))])
        _q.append('')
        return '\n'.join(_q)

    def build_genomic_pos_query(self, taxid, chr, gstart, gend):
        taxid = int(taxid)
        gstart = int(gstart)
        gend = int(gend)
        if chr.lower().startswith('chr'):
            chr = chr[3:]
        _query = {
                   "nested" : {
                       "path" : "genomic_pos",
                       "query" : {
                            "bool" : {
                                "must" : [
                                    {
                                        "term" : {"genomic_pos.chr" : chr}
                                    },
                                    {
                                        "range" : {"genomic_pos.start" : {"lte" : gend}}
                                    },
                                    {
                                        "range" : {"genomic_pos.end" : {"gte" : gstart}}
                                    }
                                ]
                            }
                        }
                    }
                }
        _query = {
            'filtered': {
                'query': _query,
                'filter' : {
                    "term" : {"taxid" : taxid}
                }
            }
        }
        _q = {'query': _query}
        if self.options:
            _q.update(self.options)
        return _q


def make_test_index():

    def get_sample_gene(gene):
        qbdr = ESQueryBuilder(fields=['_source'], size=1000)
        _query = qbdr.dis_max_query(gene)
        _query = qbdr.add_species_custom_filters_score(_query)
        _q = {'query': _query}
        if qbdr.options:
            _q.update(qbdr.options)

        esq = ESQuery()
        res = esq._search(_q)
        return [h['_source'] for h in res['hits']['hits']]

    gli = get_sample_gene('CDK2') + \
          get_sample_gene('BTK')  + \
          get_sample_gene('insulin')

    from utils.es import ESIndexer
    index_name = 'genedoc_2'
    index_type = 'gene_sample'
    esidxer = ESIndexer(None, None)
    conn = esidxer.conn
    try:
        esidxer.delete_index_type(index_type)
    except:
        pass
    mapping = dict(conn.get_mapping('gene', index_name)['gene'])
    print conn.put_mapping(index_type, mapping, [index_name])

    print "Building index..."
    cnt = 0
    for doc in gli:
        conn.index(doc, index_name, index_type, doc['_id'])
        cnt += 1
        print cnt, ':', doc['_id']
    print conn.flush()
    print conn.refresh()
    print 'Done! - {} docs indexed.'.format(cnt)

