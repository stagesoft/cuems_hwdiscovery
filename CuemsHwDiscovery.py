from subprocess import Popen, PIPE, STDOUT, CalledProcessError
from jack import Client
from Xlib import display
from Xlib.ext import xinerama

from ..XmlReaderWriter import XmlWriter
from ..log import logger

class HWDiscovery():
    '''
    Searches for the audio and video outputs through Jack and Xinerama
    and write the results in an XML
    '''
    def __init__(self):
        self.Iammaster = self.check_node_role()

        self.local_hwd()

        if self.Iammaster:
            self.network_hwd()
        else:
            self.serve_local_settings()

    def local_hwd(self):
        '''Perform local node hardware detections and records'''

        class Outputs(dict):
            pass

        outputs_object = Outputs()
        outputs_object['audio'] = {}
        outputs_object['video'] = {'outputs':{'output':[]}, 'default_output':''}
        outputs_object['dmx'] = {}

        # Audio outputs
        jc = Client('CuemsHWDiscovery')
        ports = jc.get_ports(is_audio=True, is_physical=True, is_input=True)
        if ports:
            outputs_object['audio']['outputs'] = {'output':[]}
            outputs_object['audio']['default_output'] = ''

            for port in ports:
                outputs_object['audio']['outputs']['output'].append({'name':port.name, 'mappings':{'mapped_to':[port.name, ]}})

            outputs_object['audio']['default_output'] = outputs_object['audio']['outputs']['output'][0]['name']

        # Audio inputs
        ports = jc.get_ports(is_audio=True, is_physical=True, is_output=True)
        if ports:
            outputs_object['audio']['inputs'] = {'input':[]}
            outputs_object['audio']['default_input'] = ''

            for port in ports:
                outputs_object['audio']['inputs']['input'].append({'name':port.name, 'mappings':{'mapped_to':[port.name, ]}})

            outputs_object['audio']['default_input'] = outputs_object['audio']['inputs']['input'][0]['name']

        jc.close()

        # Video
        try:
            # Xlib video outputs retreival through xinerama extension
            disp = display.Display()
            screen = disp.screen()
            window = screen.root.create_window(0, 0, 1, 1, 1, screen.root_depth)

            qs = xinerama.query_screens(window)
            if qs._data['number'] > 0:
                for index, screen in enumerate(qs._data['screens']):
                    outputs_object['video']['outputs']['output'].append({'name':f'{index}', 'mappings':{'mapped_to':[f'{index}', ]}})

        except Exception as e:
            logger.exception(e)
            outputs_object['video']['outputs'] = {'output':[]}

        if outputs_object['video']['outputs']['output']:
            outputs_object['video']['default_output'] = outputs_object['video']['outputs']['output'][0]['name']
        else:
            outputs_object['video']['default_output'] = ''

        # XML Writer
        writer = XmlWriter(schema = '/etc/cuems/project_mappings.xsd', xmlfile = '/etc/cuems/default_mappings.xml', xml_root_tag='CuemsProjectMappings')

        try:
            writer.write_from_object(outputs_object)
        except Exception as e:
            logger.exception(e)

        logger.info(f'Hardware discovery completed. Default mappings writen to {writer.xmlfile}')

    def network_hwd(self):
        '''Perform network hardware discovery, just in case I'm a master node'''

        ### REVIEW NETWORK MAP

        ### RETREIVE EACH NODE HW SETTINGS

        ### WRITE THEM ALL

        pass

    def serve_local_settings(self):
        '''Start an ip server (we'll see which protocol to use) to serve our own local 
        hardware settings with the master'''
        pass

    def check_node_role(self):
        '''Checks the role (master or slave) of the local node'''
        pass