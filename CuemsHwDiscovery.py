from subprocess import Popen, PIPE, STDOUT, CalledProcessError
from jack import Client
from Xlib import display
from Xlib.ext import xinerama
import netifaces

from ..XmlReaderWriter import XmlReader, XmlWriter
from ..log import logger
from ..cuems_nodeconf.CuemsNode import CuemsNode, CuemsNodeDict

from socket import socket, AF_INET, SOCK_STREAM
import pickle
import struct


class Outputs(dict):
    pass


class HWDiscovery():
    '''
    Searches for the audio and video outputs through Jack and Xinerama
    and write the results in an XML
    '''
    def __init__(self):
        self.xsd_path = '/etc/cuems/network_map.xsd'
        self.map_path = '/etc/cuems/network_map.xml'

        self.network_map = CuemsNodeDict()
        self.outputs_object = Outputs()
        self.my_node = None
        self.HEADER_LEN = 4


        try:
            self.my_node = self.check_node_role()
        except Exception as e:
            raise e

        self.local_hwd()

        if self.my_node.node_type == CuemsNode.NodeType.master:
            self.network_hwd()
        elif self.my_node.node_type == CuemsNode.NodeType.slave:
            self.serve_local_settings()

    def local_hwd(self):
        '''Perform local node hardware detections and records'''

        self.outputs_object = Outputs()
        self.outputs_object['audio'] = {}
        self.outputs_object['video'] = {'outputs':{'output':[]}, 'default_output':''}
        self.outputs_object['dmx'] = {}

        # Audio outputs
        jc = Client('CuemsHWDiscovery')
        ports = jc.get_ports(is_audio=True, is_physical=True, is_input=True)
        if ports:
            self.outputs_object['audio']['outputs'] = {'output':[]}
            self.outputs_object['audio']['default_output'] = ''

            for port in ports:
                self.outputs_object['audio']['outputs']['output'].append({'name':port.name, 'mappings':{'mapped_to':[port.name, ]}})

            self.outputs_object['audio']['default_output'] = self.outputs_object['audio']['outputs']['output'][0]['name']

        # Audio inputs
        ports = jc.get_ports(is_audio=True, is_physical=True, is_output=True)
        if ports:
            self.outputs_object['audio']['inputs'] = {'input':[]}
            self.outputs_object['audio']['default_input'] = ''

            for port in ports:
                self.outputs_object['audio']['inputs']['input'].append({'name':port.name, 'mappings':{'mapped_to':[port.name, ]}})

            self.outputs_object['audio']['default_input'] = self.outputs_object['audio']['inputs']['input'][0]['name']

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
                    self.outputs_object['video']['outputs']['output'].append({'name':f'{index}', 'mappings':{'mapped_to':[f'{index}', ]}})

        except Exception as e:
            logger.exception(e)
            self.outputs_object['video']['outputs'] = {'output':[]}

        if self.outputs_object['video']['outputs']['output']:
            self.outputs_object['video']['default_output'] = self.outputs_object['video']['outputs']['output'][0]['name']
        else:
            self.outputs_object['video']['default_output'] = ''

        # XML Writer
        writer = XmlWriter(schema = '/etc/cuems/project_mappings.xsd', xmlfile = '/etc/cuems/default_mappings.xml', xml_root_tag='CuemsProjectMappings')

        try:
            writer.write_from_object(self.outputs_object)
        except Exception as e:
            logger.exception(e)

        logger.info(f'Hardware discovery completed. Default mappings writen to {writer.xmlfile}')

    def network_hwd(self):
        '''Perform network hardware discovery, just in case I'm a master node'''

        ### REVIEW NETWORK MAP
        ### AND RETREIVE EACH NODE HW SETTINGS
        print('Master node retreiving hw_info from each slave node:')
        for node in self.network_map.slaves:
            try:
                print(f'Node: {node}')
                try:
                    clientsocket = socket(AF_INET, SOCK_STREAM)

                    clientsocket.connect((node.ip, node.port))
                except Exception as e:
                    raise e

                # First the header with the size coming next
                buf = ''
                while len(buf) < 4:
                    buf += clientsocket.recv(8)
                size = struct.unpack('!i', buf[:4])[0]

                print(f'Received size header from socket : {size}')

                chunks = []
                bytes_recd = 0
                # first we receive a header with the length of the object that is coming
                while bytes_recd < size:
                    chunk = clientsocket.recv(min(size - bytes_recd, 2048))
                    if chunk == b'':
                        raise RuntimeError("socket connection broken")
                    chunks.append(chunk)
                    bytes_recd = bytes_recd + len(chunk)

                data_received = chunks.join()

                object_received = pickle.loads(data_received[:size])
                print(f'Received size header from socket : {object_received}')
            except Exception as e:
                raise e

        ### WRITE THEM ALL

    def serve_local_settings(self):
        '''Start an ip server (we'll see which protocol to use) to serve our own local 
        hardware settings with the master'''
        try:
            serversocket = socket(AF_INET, SOCK_STREAM)
            serversocket.bind((self.my_node.ip, self.my_node.port))
            serversocket.listen(5)

            (clientsocket, address) = serversocket.accept()
        except Exception as e:
            raise e

        pickle_dump = pickle.dumps(self.outputs_object)

        # first the header with the length of th object
        size = len(pickle_dump)
        packed_size = struct.pack('!i', size)
        clientsocket.send(packed_size)

        # then the whole pickle
        totalsent = 0
        while totalsent < size:
            sent = clientsocket.send(pickle_dump[totalsent:])
            if sent == 0:
                raise RuntimeError("socket connection broken")
            totalsent = totalsent + sent
        
        print('Configuración enviada al master!!!')

    def check_node_role(self):
        '''Checks the role (master or slave) of the local node'''
        reader = XmlReader(schema = self.xsd_path, xmlfile = self.map_path)
        nodes = reader.read_to_objects()
        ip = self.get_ip()
        my_node = None
        for node in nodes:
            self.network_map[node.uuid] = node

            if node.node_type == 'NodeType.master':
                self.network_map[node.uuid].node_type = CuemsNode.NodeType.master
            elif node.node_type == 'NodeType.slave':
                self.network_map[node.uuid].node_type = CuemsNode.NodeType.slave
            else:
                raise Exception('Node type not recognized in network map.')

            if node.ip == ip:
                my_node = node
        
        return my_node
    
    def get_ip(self):
        iface = netifaces.gateways()['default'][netifaces.AF_INET][1]
        return netifaces.ifaddresses(iface)[netifaces.AF_INET][0]['addr']