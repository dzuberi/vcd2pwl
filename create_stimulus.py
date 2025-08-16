import argparse
import os, sys
import pickle
from vcd2pwl import Signal
import PySpice.Spice.Parser as sparser
from math import ceil

class Hierarchy:
    def __init__(self, all_signals):
        self.all_signals = all_signals
        self.hierarchy = []
        self.build_hierarchy()
        self.sort_hierarchy()
    
    def build_hierarchy(self):
        for signal in self.all_signals:
            parts = signal.split('.')
            subpath = parts[:-1]
            current = self.hierarchy
            for depth in range(len(subpath)):
                if subpath[depth] not in [c['name'] for c in current]:
                    current.append({'name': subpath[depth], 'children': []})
                    current = current[-1]['children']
                else:
                    current = [c for c in current if c['name'] == subpath[depth]][0]['children']
    
    def sort_hierarchy(self):
        def sort_children(children):
            children.sort(key=lambda x: x['name'])
            for child in children:
                sort_children(child['children'])
        
        sort_children(self.hierarchy)

    def print_item(self, item, depth):
        indent = '  ' * depth
        string = f"{indent}{item['name']}\n"
        for child in item['children']:
            string += self.print_item(child, depth + 1)
        return string

    def print_hierarchy(self, file=None):
        string = ""
        for item in self.hierarchy:
            string += self.print_item(item, 0)
        if not file:
            print(string)
        else:
            with open(file, 'w') as f:
                f.write(string)

class StimulusWriter:
    def __init__(self, pwl_dir, module_path, sim_length, verilog_file=None, spice_file=None):
        self.module_path = module_path
        self.pwl_dir = pwl_dir
        self.verilog_file = verilog_file
        self.spice_file = spice_file
        self.sim_length = sim_length
        self.load_pickle()
    
    def get_ports_from_spice_file(self):
        # Parse the SPICE file to extract subcircuit ports
        # assumes that the file name is something like <subcircuit_name>.<extension>
        spice_parser = sparser.SpiceParser(self.spice_file)
        subcircuits = spice_parser.subcircuits
        subcircuit_ports = {subcircuit.name: subcircuit.nodes for subcircuit in subcircuits}
        spice_ports = subcircuit_ports[self.spice_file.split('/')[-1].split('.')[0]]
        print(f"Subcircuit ports for {self.spice_file}: {spice_ports}")
        self.spice_ports = spice_ports
    
    def get_ports_from_verilog_file(self):
        # Parse the Verilog file to determine which ports are inputs and which are outputs
        # assumes that the file name is something like <module_name>.<extension>
        port_directions = {}
        from pyverilog.vparser.parser import parse
        from pyverilog.vparser.ast import ModuleDef, Input, Output, Inout, Decl
        ast, _ = parse([self.verilog_file])
        for desc in ast.description.definitions:
            if isinstance(desc, ModuleDef):
                print(f"Module: {desc.name}")
                # Build a mapping from port name to direction
                for item in desc.items:
                    if isinstance(item, Decl):
                        for d in item.children():
                            if isinstance(d, Input):
                                port_directions[d.name] = 'input'
                            elif isinstance(d, Output):
                                port_directions[d.name] = 'output'
                            elif isinstance(d, Inout):
                                port_directions[d.name] = 'inout'
                # Print port name and direction
                for port in desc.portlist.ports:
                    direction = port_directions.get(port.name, 'unknown')
                    print(f"Port: {port.name}, Direction: {direction}")
        self.verilog_ports = port_directions
    
    def get_common_ports(self):
        s_v = set(self.verilog_ports.keys())
        s_s = set([s.split('.')[0] for s in self.spice_ports]) # bigspicy seems to use names like s.0 for s[0]
        spice_only = s_s - s_v
        verilog_only = s_v - s_s
        assert(not verilog_only), f"Verilog ports not found in spice: {verilog_only}"
        if spice_only:
            print(Warning(f"SPICE ports not found in Verilog: {spice_only}\nThis could be intended, especially for power pins"))
        self.common_ports = {p:d for p,d in self.verilog_ports.items() if p in s_s}
        self.input_ports = [p for p,d in self.common_ports.items() if d == 'input' or d == 'inout']
        print(f"SPICE Ports:\n{self.spice_ports}\nVerilog Input Ports:{list(self.verilog_ports.items())}\nCommon Ports:{list(self.common_ports.items())}")

    def get_pwls(self):
        print(self.signal_name_table)
        self.undriven_spice_ports = set(self.spice_ports)
        self.driver_code = ''
        for signal in self.input_ports:
            if signal not in self.signal_name_table:
                print(f"ERROR: signal {signal} found in verilog but not in .vcd file. This is unexpected")
                sys.exit(1)
            signal_obj = self.signal_name_table[signal]
            bitwidth = signal_obj.size
            id = self.signal_to_id[signal_obj]
            print(signal, bitwidth)
            for bit in range(bitwidth):
                pwl_file = self.pwl_dir + '/' + str(self.id_to_file[(id, bit)]) + '.pwl'
                print(f"Loading PWL file for signal {signal}.{bit}: {pwl_file}")
                spice = f"V{signal}.{bit} {signal}.{bit} 0 PWL FILE=\"{pwl_file}\"\n"
                self.driver_code += (spice)
                self.undriven_spice_ports.remove(f"{signal}.{bit}")
        # print(f"driver code so far:\n{self.driver_code}\nundriven: {self.undriven_spice_ports}")
        self.build_rest_of_spice()
        
    def build_rest_of_spice(self):
        print(self.sim_length)
        tran_statement = f'.tran 1p {ceil(self.sim_length / 1e-12)}p'
        spice_module_name = self.spice_file.split("/")[-1].split('.')[0]
        spice_directory = os.path.dirname(self.spice_file) if self.spice_file else '.'
        include_statement = f'.include "{spice_module_name}.sp"'
        instantiation = f'X{spice_module_name} ' + ' '.join(self.spice_ports) + f' {spice_module_name}'
        print_statement = f'.print tran {" ".join([f"v({s})" for s in self.spice_ports])}'

        vdd_port_names = ['vdd','VDD'] # Common names for VDD
        vdd_port = None
        vss_port_names = ['vss','VSS'] # Common names for VSS
        vss_port = None
        for name in vdd_port_names:
            if name in self.spice_ports:
                vdd_port = name
                break
        for name in vss_port_names:
            if name in self.spice_ports:
                vss_port = name
                break
        if not vdd_port or not vss_port:
            print("Cannot find power ports")
            sys.exit(1)
        
        power_statements = f"{vss_port} {vss_port} 0 0\n{vdd_port} {vdd_port} 0 3.3\n"

        self.driver_code = f"{include_statement}\n{instantiation}\n{tran_statement}\n{self.driver_code}{power_statements}{print_statement}\n.end"

        print(f"driver code so far:\n{self.driver_code}\nundriven: {self.undriven_spice_ports}")
        

    def load_pickle(self):
        dicts = pickle.load(open(os.path.join(self.pwl_dir, "dicts.pickle"), "rb"))
        self.id_to_signal = dicts['id_to_signal']
        self.file_to_id = dicts['file_to_id']
        self.id_to_file = {id:f for f,id in self.file_to_id.items()}
        self.signal_to_id = {v: k for k, v_ in self.id_to_signal.items() for v in v_} # map of signal names to id
        self.all_signals = [s.name for s in self.signal_to_id.keys()] # all signals in symbol table
        # print(self.all_signals)
        self.signals = [s for s in self.signal_to_id.keys() if '.'.join(s.name.split('.')[:-1]) == self.module_path] # all signals in subpath
        self.signal_name_table = {list(reversed(s.name.split('.')))[0]: s for s in self.signals}


    def ports_to_signals(self):
        pass

def main():
    parser = argparse.ArgumentParser(description="Generate SPICE stimulus from PWL files.")
    parser.add_argument("--pwl_dir", default="pyvcd_pwls", help="Directory to read PWL files from")
    parser.add_argument("--spice_file", default=None, help="Input SPICE file to read")
    parser.add_argument("--verilog_file", default=None, help="Input Verilog file to read")
    parser.add_argument("module_path", help="Name of the stimulus to generate")
    args = parser.parse_args()

    # load data structures from the PWL generation script
    dicts = pickle.load(open(os.path.join(args.pwl_dir, "dicts.pickle"), "rb"))
    id_to_signal = dicts['id_to_signal']
    file_to_id = dicts['file_to_id']
    sim_length = dicts['sim_length']
    signal_to_id = {v: k for k, v_ in id_to_signal.items() for v in v_} # map of signal names to id
    all_signals = [s.name for s in signal_to_id.keys()] # all signals in symbol table
    signals = [s for s in signal_to_id.keys() if '.'.join(s.name.split('.')[:-1]) == args.module_path] # all signals in subpath
    files = [f for f in file_to_id.keys()]

    # convert the signals from the dict into a hierarchy for sanity/debug
    hierarchy = Hierarchy(all_signals)
    hierarchy.print_hierarchy('hier.txt')

    for signal in signals:
        print(signal)

    writer = StimulusWriter(
        pwl_dir=args.pwl_dir, 
        module_path=args.module_path, 
        sim_length=sim_length, 
        verilog_file=args.verilog_file, 
        spice_file=args.spice_file
    )
    if args.spice_file:
        writer.get_ports_from_spice_file()

    if args.verilog_file:
        writer.get_ports_from_verilog_file()

    writer.get_common_ports()
    writer.get_pwls()


"""
https://pyspice.fabrice-salvaire.fr/releases/v1.3/api/PySpice/Spice/Parser.html
fle6 example prga_tb_top.i_postimpl.dut.i_tile_x1y5.i_tile_x0y0.i_blk.i_cluster_i0
"""


if __name__ == "__main__":
    main()