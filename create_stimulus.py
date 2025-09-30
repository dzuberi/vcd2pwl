import argparse
import os, sys, shutil
import pickle
from vcd2pwl import Signal, Value, PWLConverter, VCDIngest
import PySpice.Spice.Parser as sparser
from math import ceil
from pprint import pprint

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
    def __init__(self, pwl_dir, module_path, sim_length, pwlconv, verilog_file=None, spice_file=None, prog_done_path=None, output_dir='./spice'):
        self.module_path = module_path
        self.pwl_dir = pwl_dir
        self.verilog_file = verilog_file
        self.spice_file = spice_file
        self.sim_length = sim_length
        self.prog_done_path = prog_done_path
        self.output_dir = output_dir
        self.operating_voltage = 1.8
        self.pwlconv = pwlconv
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
        spice_module_name = self.spice_file.split("/")[-1].split('.')[0]
        for signal in self.input_ports:
            if signal not in self.signal_name_table:
                print(f"ERROR: signal {signal} found in verilog but not in .vcd file. This is unexpected")
                sys.exit(1)
            signal_obj = self.signal_name_table[signal]
            bitwidth = signal_obj.size
            id = self.signal_to_id[signal_obj]
            print(signal, bitwidth)
            for bit in range(bitwidth):
                if bitwidth == 1:
                    signal_name = signal
                else:
                    signal_name = f"{signal}.{bit}"
                pwl_file = self.pwl_dir + '/' + str(self.id_to_file[(id, bit)]) + '.pwl'
                # print(f"Loading PWL file for signal {signal_name}: {pwl_file}")
                spice = f"V{signal_name} {signal_name} 0 PWL FILE \"{pwl_file.split('/')[-1]}\"\n"
                self.driver_code += (spice)
                self.undriven_spice_ports.remove(f"{signal_name}")
        # set initial conditions
        self.driver_code += '* Initial Conditions\n.IC '
        for signal in self.signals:
            signal_obj = signal
            bitwidth = signal_obj.size
            id = self.signal_to_id[signal_obj]
            for bit in range(bitwidth):
                if bitwidth == 1:
                    signal_name = signal.name.split('.')[-1]
                else:
                    signal_name = f"{signal.name.split('.')[-1]}.{bit}"
                analog_value_table = self.pwlconv.analog_value_table[(id, bit)]
                print(f"Setting initial condition for signal {signal_name}: {analog_value_table[0].value}")
                if signal.name.split('.')[-1] in self.input_ports:
                    spice = f"V({signal_name})={analog_value_table[0].value} "
                else:
                    spice = f"V(X{spice_module_name}:{signal_name})={analog_value_table[0].value} "
                self.driver_code += (spice)
        self.driver_code += '\n'
            
        # print(f"driver code so far:\n{self.driver_code}\nundriven: {self.undriven_spice_ports}")
        self.build_rest_of_spice()
        
    def build_rest_of_spice(self):
        print(self.sim_length)
        options_statements = ".options linsol type=aztecoo"
        tran_statement = f'.tran 1p {ceil(self.sim_length / 1e-12)}p uic'
        spice_module_name = self.spice_file.split("/")[-1].split('.')[0]
        spice_directory = os.path.dirname(self.spice_file) if self.spice_file else '.'
        include_statement = f'.include "{spice_module_name}.sp"'
        instantiation = f'X{spice_module_name} ' + ' '.join(self.spice_ports) + f' {spice_module_name}'
        print_statement = f'.print tran {" ".join([f"v(X{spice_module_name}:{s})" for s in self.spice_ports])}'

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
            if not vdd_port:
                vdd_port = 'VDD'
            if not vss_port:
                vss_port = 'VSS'
        
        power_statements = f".global {vdd_port}\n{vss_port} {vss_port} 0 0\n{vdd_port} {vdd_port} 0 {str(self.operating_voltage)}\n"

        self.driver_code = f"**generated by create_stimulus.py\n{options_statements}\n{include_statement}\n{instantiation}\n{tran_statement}\n{self.driver_code}{power_statements}{print_statement}\n.end"

        print(f"driver code so far:\n{self.driver_code}\nundriven: {self.undriven_spice_ports}")
    
    def make_outputs(self, lib_dir='.', stimulus_file_name='top.sp'):
        spice_file = self.spice_file
        pm_files = [f for f in os.listdir(lib_dir) if f.endswith('.pm')]
        spice_lib_files = [f for f in os.listdir(lib_dir) if f.endswith('.sp') or f.endswith('.spice')]
        os.makedirs(self.output_dir, exist_ok=True)
        for file in pm_files:
            shutil.copy(os.path.join(lib_dir, file), self.output_dir)
        for file in spice_lib_files:
            with open(os.path.join(lib_dir, file), 'r') as f:
                content = f.read()
            # content = '\n'.join([f'.include {f}' for f in pm_files]) + '\n' + content
            with open(os.path.join(self.output_dir, file), 'w') as f:
                f.write(content)
        with open(spice_file, 'r') as f:
            content = f.read()
        with open(os.path.join(self.output_dir, spice_file.split('/')[-1]), 'w') as f:
            content = '\n'.join([f'.include {f}' for f in spice_lib_files+pm_files]) + '\n' + content
            f.write(content)
        with open(os.path.join(self.output_dir, stimulus_file_name), 'w') as f:
            f.write(self.driver_code)
        

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
        self.sim_length = max(v.time for values in self.pwlconv.analog_value_table.values() for v in values) + 1e-9 # add 1ns to ensure that the last value is included in the simulation

    def remove_programming(self):
        if not self.prog_done_path:
            return
        # remove all stimulus before the prog_done_path signal goes high
        prog_done_signal = [s for s in self.signal_to_id.keys() if s.name == self.prog_done_path]
        if not prog_done_signal:
            print(f"WARNING: prog_done_path {self.prog_done_path} not found in signals. Ignoring.")
            import pdb; pdb.set_trace()
            return
        prog_done_signal = prog_done_signal[0]
        id = self.signal_to_id[prog_done_signal]
        bitwidth = prog_done_signal.size
        assert (bitwidth == 1), f"prog_done_path {self.prog_done_path} has bitwidth {bitwidth}, but should be 1"
        analog_values = self.pwlconv.analog_value_table[(id, 0)]
        self.prog_done_time = None
        for v in analog_values:
            if v.value != 0:
                self.prog_done_time = v.time
                break
        if not self.prog_done_time:
            print(f"WARNING: prog_done_path {self.prog_done_path} never goes high. Ignoring.")
            return
        print(f"Removing all stimulus before prog_done_path {self.prog_done_path} goes high at time {self.prog_done_time}.")
        print(analog_values)
        for signal in self.signals:
            id = self.signal_to_id[signal]
            bitwidth = signal.size
            for bit in range(bitwidth):
                assert max([v.time for v in self.pwlconv.analog_value_table[(id, bit)]]) <= self.sim_length, f"Signal {signal} bit {bit} has values {max([v.time for v in self.pwlconv.analog_value_table[(id,bit)]])} beyond sim_length {self.sim_length}"
                values = [Value(v.value, v.time - self.prog_done_time) for v in self.pwlconv.analog_value_table[(id, bit)]]
                values = sorted(values, key=lambda x: x.time)
                assert (sum([v.time < 0 for v in values]) > 0), f"Signal {signal} bit {bit} has no values before prog_done_time {self.self.prog_done_time}. This is unexpected because each signal should have a value at time 0.\nValues: {[str(v) for v in values]}\nself.pwlconv.analog_value_table[(id, bit)]: {[str(v) for v in self.pwlconv.analog_value_table[(id, bit)]]}"
                positive_times = [v for v in values if v.time > 0]
                negative_times = [v for v in values if v.time <= 0]
                if negative_times:
                    # if negative_times[0].time == 0:
                    #     import pdb; pdb.set_trace()
                    negative_times = negative_times[-1:]
                    negative_times[0].time = 0
                new_values = negative_times + positive_times
                assert(new_values[0].time == 0), f"First value of signal {signal} after removing programming is not at time 0, but at {new_values[0].time}"
                # print(f'values: {[str(v) for v in values]} became {[str(v) for v in new_values]}')
                assert new_values[-1].time <= self.sim_length - self.prog_done_time, f"Last value of signal {signal} after removing programming is at time {new_values[-1].time}, which is beyond the new sim_length {self.sim_length - self.prog_done_time}"
                self.pwlconv.dump_pwl(f"{self.id_to_file[(id, bit)]}.pwl", self.output_dir, new_values)
                self.pwlconv.analog_value_table[(id, bit)] = new_values # MAKE SURE NOT TO SAVE THIS
        self.sim_length = self.sim_length - self.prog_done_time


    def ports_to_signals(self):
        pass

def main():
    parser = argparse.ArgumentParser(description="Generate SPICE stimulus from PWL files.")
    parser.add_argument("--pwl_dir", default="pyvcd_pwls", help="Directory to read PWL files from")
    parser.add_argument("--spice_file", default=None, help="Input SPICE file to read")
    parser.add_argument("--verilog_file", default=None, help="Input Verilog file to read")
    parser.add_argument("--spice_libs", default='../lib', help="Location of SPICE library files (PDK)")
    parser.add_argument("--prog_done_path", default=None, help="Path to signal that indicates that FPGA programming is done")
    parser.add_argument("--output_dir", default="./spice", help="Directory to write output SPICE files to")
    parser.add_argument("module_path", help="Name of the stimulus to generate")
    args = parser.parse_args()

    # load data structures from the PWL generation script
    dicts = pickle.load(open(os.path.join(args.pwl_dir, "dicts.pickle"), "rb"))
    id_to_signal = dicts['id_to_signal']
    file_to_id = dicts['file_to_id']
    sim_length = dicts['sim_length']
    pwlconv = dicts['obj']
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
        pwlconv=pwlconv,
        verilog_file=args.verilog_file, 
        spice_file=args.spice_file,
        prog_done_path=args.prog_done_path,
        output_dir=args.output_dir
    )
    if args.spice_file:
        writer.get_ports_from_spice_file()

    if args.verilog_file:
        writer.get_ports_from_verilog_file()

    writer.get_common_ports()
    writer.remove_programming()
    writer.get_pwls()
    writer.make_outputs(
        lib_dir = args.spice_libs,
        stimulus_file_name=f'{args.spice_file.split("/")[-1].split(".")[0]}_top.sp' if args.spice_file else 'top.sp'
    )


"""
https://pyspice.fabrice-salvaire.fr/releases/v1.3/api/PySpice/Spice/Parser.html
fle6 example prga_tb_top.i_postimpl.dut.i_tile_x1y5.i_tile_x0y0.i_blk.i_cluster_i0
"""


if __name__ == "__main__":
    main()