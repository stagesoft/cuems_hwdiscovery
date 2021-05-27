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
from time import sleep
from datetime import datetime
from os import path, system


class Outputs(dict):
    def __init__(self):
        self.number_of_nodes = 1

        super(Outputs, self).__init__({'default_audio_input' : "", 'default_audio_output' : "", 'default_video_input' : "", 'default_video_output' : "", 'default_dmx_input' : "", 'default_dmx_output' : ""})

        self.nodes = []

    @property
    def number_of_nodes(self):
        return super().__getitem__('number_of_nodes')

    @number_of_nodes.setter
    def number_of_nodes(self, number_of_nodes):
        super().__setitem__('number_of_nodes', number_of_nodes)

    @property
    def nodes(self):
        return super().__getitem__('nodes')

    @nodes.setter
    def nodes(self, nodes):
        super().__setitem__('nodes', nodes)

class CuemsHWDiscovery():
    '''
    Searches for the audio and video outputs through Jack and Xinerama
    and write the results in an XML
    '''
    MAX_SLAVE_CONNECTION_RETRIES = 5

    def __init__(self):
        self.network_map = CuemsNodeDict()
        self.outputs_object = Outputs()
        self.my_node = None
        self.HEADER_LEN = 4

        self.outputs_object = Outputs()

        try:
            self.my_node = self.check_node_role()
        except Exception as e:
            raise e

        self.local_hwd()

        if self.my_node.node_type == CuemsNode.NodeType.master:
            try:
                self.network_hwd()
            except Exception as e:
                logger.exception(f'Exception during network slaves hw discovery: {e}')
                
            # Update number of nodes after discovery the network
            self.outputs_object.number_of_nodes = len(self.outputs_object.nodes)

            logger.debug(f"I'm the MASTER, this is all the hwd discovered on the net:\n {self.outputs_object}")
        elif self.my_node.node_type == CuemsNode.NodeType.slave:
            logger.debug(f"I'm a SLAVE, this is my local hwd discovered:\n {self.outputs_object}")
            logger.debug(self.outputs_object)
            self.serve_local_settings()

        self.write_mappings_file()

    def local_hwd(self):
        '''Perform local node hardware detections and records'''

        # Audio
        temp_node_dict = {'node' : {'uuid': self.my_node.uuid, 'mac' : self.my_node.mac}}
        temp_dict = {}

        # Audio outputs
        jc = Client('CuemsHWDiscovery')
        ports = jc.get_ports(is_audio=True, is_physical=True, is_input=True)
        if ports:
            temp_dict['outputs'] = {'output':[]}

            for port in ports:
                temp_dict['outputs']['output'].append({'name':f'{port.name}', 'mappings':{'mapped_to':[f'{port.name}', ]}})

            self.outputs_object['default_audio_output'] = f"{self.my_node.uuid}_{temp_dict['outputs']['output'][0]['name']}"

        # Audio inputs
        ports = jc.get_ports(is_audio=True, is_physical=True, is_output=True)
        if ports:
            temp_dict['inputs'] = {'input':[]}

            for port in ports:
                temp_dict['inputs']['input'].append({'name':f'{port.name}', 'mappings':{'mapped_to':[f'{port.name}', ]}})

            self.outputs_object['default_audio_input'] = f"{self.my_node.uuid}_{temp_dict['inputs']['input'][0]['name']}"

        jc.close()

        temp_node_dict['node']['audio'] = temp_dict

        # Video
        temp_dict = {'outputs':{'output':[]}}

        try:
            # Xlib video outputs retreival through xinerama extension
            disp = display.Display()
            screen = disp.screen()
            window = screen.root.create_window(0, 0, 1, 1, 1, screen.root_depth)

            qs = xinerama.query_screens(window)
            if qs._data['number'] > 0:
                for index, screen in enumerate(qs._data['screens']):
                    temp_dict['outputs']['output'].append({'name':f'{index}', 'mappings':{'mapped_to':[f'{index}', ]}})

        except Exception as e:
            logger.exception(e)
            temp_dict['outputs'] = {'output':[]}

        if temp_dict['outputs']['output']:
            self.outputs_object['default_video_output'] = f"{self.my_node.uuid}_{temp_dict['outputs']['output'][0]['name']}"

        temp_node_dict['node']['video'] = temp_dict

        # DMX
        temp_node_dict['node']['dmx'] = {}

        # Append this node to the node list
        self.outputs_object['nodes'].append(temp_node_dict)

    def network_hwd(self):
        '''Perform network hardware discovery, just in case I'm a master node'''

        ### REVIEW NETWORK MAP
        ### AND RETREIVE EACH NODE HW SETTINGS
        logger.info('Master node retreiving hw_info from each slave node:')
        for node in self.network_map.slaves:
            object_received = None
            try:
                retries = 0
                while retries < self.MAX_SLAVE_CONNECTION_RETRIES:
                    try:
                        clientsocket = socket(AF_INET, SOCK_STREAM)

                        clientsocket.connect((node.ip, node.port))
                    except ConnectionRefusedError as e:
                        retries += 1
                        sleep(1)
                    except Exception as e:
                        logger.exception(e)
                        raise e
                    else:
                        break

                if retries == self.MAX_SLAVE_CONNECTION_RETRIES:
                    logger.warning(f'WARNING: Connection with node {node.mac} refused')
                    break

                # First the header with the size coming next
                buf = ''
                while len(buf) < 4:
                    buf = clientsocket.recv(4)
                size = struct.unpack('!i', buf[:4])[0]
                logger.info(f'Slave {node.mac} sent header: {size}')

                chunks = []
                bytes_recd = 0
                # first we receive a header with the length of the object that is coming
                try:
                    while bytes_recd < size:
                        try:
                            chunk = clientsocket.recv(min(size - bytes_recd, 2048))
                        except Exception as e:
                            logger.exception(e)
                            raise e

                        if chunk == b'':
                            raise RuntimeError("Socket connection broken")
                        
                        chunks.append(chunk)
                        bytes_recd = bytes_recd + len(chunk)
                except Exception as e:
                    logger.exception(e)
                    raise e

                data_received = b''.join(chunks)

                object_received = pickle.loads(data_received[:size])
                logger.info(f'Slave {node.mac} sent mappings object: {object_received}')
            except Exception as e:
                logger.exception(e)

            ### JOIN RECEIVED MAP WITH LOCAL
            if object_received:
                self.outputs_object['nodes'].extend(object_received['nodes'])

    def serve_local_settings(self):
        '''Start an ip server (we'll see which protocol to use) to serve our own local 
        hardware settings with the master'''
        try:
            serversocket = socket(AF_INET, SOCK_STREAM)
            serversocket.bind((self.my_node.ip, self.my_node.port))
            serversocket.listen(5)

            logger.info("Oh, Master, I'm here waiting for you... to connect...")

            (clientsocket, address) = serversocket.accept()
        except Exception as e:
            logger.exception(e)
            raise e

        pickle_dump = pickle.dumps(self.outputs_object)

        # First the header with the length of the object
        size = len(pickle_dump)
        packed_size = struct.pack('!i', size)
        clientsocket.send(packed_size)

        # Then the whole pickle
        totalsent = 0
        while totalsent < size:
            sent = clientsocket.send(pickle_dump[totalsent:])
            if sent == 0:
                raise RuntimeError("socket connection broken")
            totalsent = totalsent + sent

        logger.info(f'Sent {totalsent} bytes')
        logger.info('Local mappings configuration sent to master node!')

        try:
            # socket.shutdown(serversocket)
            serversocket.close()
        except Exception as e:
            logger.exception(e)

    def write_mappings_file(self):
        xmlfile = '/etc/cuems/default_mappings.xml'

        # Back up previous mappings file
        if path.exists(xmlfile):
            try:
                system(f'cp {xmlfile} {xmlfile}_bak_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xml')
            except Exception as e:
                logger.exception(f'Raised exception while creating default_mappings.xml back up copy: {e}')

        # XML Writer
        writer = XmlWriter(schema = '/etc/cuems/project_mappings.xsd', xmlfile = xmlfile, xml_root_tag='CuemsProjectMappings')

        try:
            writer.write_from_object(self.outputs_object)
        except Exception as e:
            logger.exception(e)
            print('No se ha podido guardar el fichero de mappings')
            exit(-1)

        logger.info(f'Hardware discovery completed. Default mappings writen to {writer.xmlfile}')

    def check_node_role(self):
        xsd_path = '/etc/cuems/network_map.xsd'
        map_path = '/etc/cuems/network_map.xml'

        '''Checks the role (master or slave) of the local node'''
        reader = XmlReader(schema = xsd_path, xmlfile = map_path)
        nodes = reader.read_to_objects()
        ip = self.get_ip()
        my_node = None
        for node in nodes:
            self.network_map[node.mac] = node

            if node.node_type == 'NodeType.master':
                self.network_map[node.mac].node_type = CuemsNode.NodeType.master
            elif node.node_type == 'NodeType.slave':
                self.network_map[node.mac].node_type = CuemsNode.NodeType.slave
            else:
                raise Exception('Node type not recognized in network map.')

            if node.ip == ip:
                my_node = node
        
        return my_node
    
    def get_ip(self):
        iface = netifaces.gateways()['default'][netifaces.AF_INET][1]
        return netifaces.ifaddresses(iface)[netifaces.AF_INET][0]['addr']