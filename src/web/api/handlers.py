# -*- coding: utf-8 -*-
from biothings.web.api.es.handlers import BiothingHandler
from biothings.web.api.es.handlers import MetadataHandler
from biothings.web.api.es.handlers import QueryHandler
from biothings.web.api.es.handlers import StatusHandler
from tornado.web import RequestHandler

def get_es_index(inst, options):
    if 'all' in options.esqb_kwargs.species or len(set(options.esqb_kwargs.species)-
        set([x['tax_id'] for x in inst.web_settings.TAXONOMY.values()])) > 0:
        return inst.web_settings.ES_INDEX
    else:
        return inst.web_settings.ES_INDEX_TIER1

class GeneHandler(BiothingHandler):
    ''' This class is for the /gene endpoint. '''
    def _get_es_index(self, options):
        return get_es_index(self, options)

class QueryHandler(QueryHandler):
    ''' This class is for the /query endpoint. '''
    def _get_es_index(self, options):
        return get_es_index(self, options)

class StatusHandler(StatusHandler):
    ''' This class is for the /status endpoint. '''
    pass

class MetadataHandler(MetadataHandler):
    ''' This class is for the /metadata endpoint. '''
    pass

class TaxonHandler(RequestHandler):
    def get(self, taxid):
        self.redirect("http://t.biothings.io/v1/taxon/%s?include_children=1" % taxid)

class DemoHandler(RequestHandler):
    def get(self):
        with open('../docs/demo/index.html', 'r') as demo_file: 
            self.write(demo_file.read())
    