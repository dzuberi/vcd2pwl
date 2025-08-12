import argparse
import os
import pickle
from vcd2pwl import Signal
import PySpice.Spice.Parser as sparser

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
    signal_to_id = {v: k for k, v_ in id_to_signal.items() for v in v_}
    all_signals = [s.name for s in signal_to_id.keys()]
    signals = [s for s in signal_to_id.keys() if '.'.join(s.name.split('.')[:-1]) == args.module_path]
    files = [f for f in file_to_id.keys()]

    # convert the signals from the dict into a hierarchy for sanity/debug
    hierarchy = Hierarchy(all_signals)
    hierarchy.print_hierarchy('hier.txt')

    for signal in signals:
        print(signal)
    if args.spice_file:
        # Parse the SPICE file to extract subcircuit ports
        spice_parser = sparser.SpiceParser(args.spice_file)
        subcircuits = spice_parser.subcircuits
        subcircuit_ports = {subcircuit.name: subcircuit.nodes for subcircuit in subcircuits}
        spice_ports = subcircuit_ports[args.spice_file.split('/')[-1].split('.')[0]]
        print(f"Subcircuit ports for {args.spice_file}: {spice_ports}")

    if args.verilog_file:
        # Parse the Verilog file to determine which ports are inputs and which are outputs
        port_directions = {}
        from pyverilog.vparser.parser import parse
        ast, _ = parse([args.verilog_file])
        from pyverilog.vparser.ast import ModuleDef, Input, Output, Inout, Decl
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


"""
https://pyspice.fabrice-salvaire.fr/releases/v1.3/api/PySpice/Spice/Parser.html
fle6 example prga_tb_top.i_postimpl.dut.i_tile_x1y5.i_tile_x0y0.i_blk.i_cluster_i0
"""


if __name__ == "__main__":
    main()