import argparse
import os
import re
from collections import defaultdict
import vcd.reader as pyvcd
import pickle
import sys
from tqdm import tqdm
import subprocess

def get_dir_size_du(path):
    result = subprocess.run(['du', '-sh', path], stdout=subprocess.PIPE, text=True)
    return result.stdout.strip()

def sanitize_filename(name):
    # Replace any character not allowed in Linux file names with _
    # Allowed: letters, numbers, dot, dash, underscore
    return re.sub(r'[^A-Za-z0-9._-]', '_', name)

class Signal:
    def __init__(self, type, size, id, name):
        self.type = type
        self.size = size
        self.id = id
        self.name = name
    def __str__(self):
        if self.size > 1:
            return f"{self.type}:{self.name}[{self.size-1}:0]"
        return f"{self.type}:{self.name}"
    def __eq__(self, other):
        if not isinstance(other, Signal):
            return NotImplemented
        return self.type == other.type and self.size == other.size and self.id == other.id and self.name == other.name
    def __hash__(self):
        return hash((self.type, self.size, self.id, self.name))

class Value:
    def __init__(self, value, time):
        self.value = value
        self.time = time
        if type(self.value) is str:
            assert(all(v in ['0', '1', 'x', 'z'] for v in self.value)), \
                f"Value must be a string of '0', '1', 'x', or 'z', got {self.value}"
    def get_bit_value(self, bit):
        if type(self.value) is str:
            if len(self.value) == 1:
                return Value(self.value, self.time)
            else: 
                return Value(self.value[-1 - bit], self.time)
        else:
            print(f"Error: Value {self.value} is not a string")
            raise TypeError("Value must be a string of '0', '1', 'x', or 'z'")

    def copy(self):
        return Value(self.value, self.time)
    def __str__(self):
        return f"{self.value} @ {self.time}"
    def __eq__(self, other):
        if not isinstance(other, Value):
            return NotImplemented
        return self.value == other.value and self.time == other.time

class PWLConverter:
    def __init__(self, vcd):
        self.value_table = defaultdict(list)
        self.HI = 1.8       # Electrical level for a logical 1
        self.LO = 0.0       # Electrical value for a logical 0
        self.TRF = 1e-9     # Rise/fall time in sec
        self.Z = 'z'
        self.vcd = vcd # VCDIngest instance
    
    def convert(self):
        # Convert the VCD data to PWL format
        self.clean_dvt() # doesn't seem necessary
        self.analyze_fastest_transition()
        self.convert_dvt_to_analog()

    def clean_dvt(self):
        """ Clean duplicate values in the digital value table. """
        self.digital_value_table = defaultdict(list)
        for id,values in self.vcd.value_table.items():
            if not values:
                print(f"Warning: No values for signal {id}. Adding an X.")
                self.digital_value_table[id].append(Value('x', 0))
                continue
            self.digital_value_table[id].append(values[0].copy())
            for i in range(1, len(values)):
                if values[i] != values[i-1]:
                    self.digital_value_table[id].append(values[i].copy())
            if self.digital_value_table[id][0].time != 0:
                self.digital_value_table[id].insert(0, Value(self.digital_value_table[id][0].value, 0))

    def analyze_fastest_transition(self):
        fastest_transition = float('inf')
        for id,values in self.digital_value_table.items():
            for i in range(1, len(values)):
                transition_time = values[i].time - values[i-1].time
                if transition_time < fastest_transition:
                    fastest_transition = transition_time
                if transition_time == 0:
                    print(f"Warning: Zero transition time detected for signal {id}: {values[i-1]} -> {values[i]}")
        if(fastest_transition * self.vcd.timescale < 2*self.TRF):
            print(f"Fastest transition time {fastest_transition * self.vcd.timescale} is less than 2x the rise/fall time {self.TRF}.")
            print("This may lead to inaccurate PWL generation.")
            sys.exit(1)
    
    def digital_to_analog(self, value):
        return self.LO if value == '0' or value == 'x' else self.HI if value == '1' else self.Z

    def convert_dvt_to_analog(self):
        self.analog_value_table = defaultdict(list)
        for id, all_values in self.digital_value_table.items():
            assert(all([signal_width == self.vcd.symbol_table[id][0].size for signal_width in [s.size for s in self.vcd.symbol_table[id]]]))
            signal_width = self.vcd.symbol_table[id][0].size
            # print(f"Converting signal {id} with width {signal_width} to analog values.")
            # signal_width = self.vcd.symbol_table[id].size
            for bit in range(signal_width):
                # print(f"Processing bit {bit} of signal {id}: {(id, bit)}.")
                values = [value.get_bit_value(bit) for value in all_values]
                time = values[0].time * self.vcd.timescale
                value = self.digital_to_analog(values[0].value)
                if value != self.Z:
                    self.analog_value_table[(id, bit)].append(Value(value, time))
                # else:
                #     print(f"Warning: Initial value for signal {id} bit {bit} is Z. Skipping initial value.")
                for i in range(1, len(values)):
                    if values[i].value == values[i-1].value:
                        continue
                    time = values[i].time * self.vcd.timescale
                    prev_value = self.digital_to_analog(values[i-1].value)
                    new_value = self.digital_to_analog(values[i].value)
                    if prev_value != self.Z:
                        self.analog_value_table[(id, bit)].append(Value(prev_value, time))
                    if new_value != self.Z:
                        self.analog_value_table[(id, bit)].append(Value(new_value, time + self.TRF))
    
    def dump_pwls(self, pwl_dir="pwls", submodule="prga_tb_top.i_postimpl.dut"):
        os.makedirs(pwl_dir, exist_ok=True)
        # print(sorted(list(self.analog_value_table.keys()), key=lambda x: x[1], reverse=False))
        file_to_id ={i: id for i, id in enumerate(self.analog_value_table.keys())}
        id_to_file = {id: i for i, id in file_to_id.items()}
        data = {
            'id_to_signal': self.vcd.symbol_table,
            'file_to_id': file_to_id,
            'sim_length': self.vcd.time * self.vcd.timescale,
        }
        pickle.dump(data, open(os.path.join(pwl_dir, "dicts.pickle"), "wb"))
        for id, values in tqdm(self.analog_value_table.items(), desc="Writing PWL files", unit="file"):
            file_name = f"{id_to_file[id]}.pwl"
            file_path = os.path.join(pwl_dir, file_name)
            # import pdb; pdb.set_trace()
            # if self.vcd.symbol_table[id[0]][0].size > 1:
                # print(f"Writing {file_path} with {len(values)} values for key {id} corresponding to signal {self.vcd.symbol_table[id[0]][0].name} with length {self.vcd.symbol_table[id[0]][0].size}.")
            with open(file_path, "w") as fid:
                for value in values:
                    fid.write(f"{value.time} {value.value}\n")
        print(f"Dumped {len(self.analog_value_table)} PWL files to {pwl_dir}. Total size: {get_dir_size_du(pwl_dir)}")
        

class VCDIngest:
    def __init__(self):
        self.current_scope = []
        self.timescale = 1  # Default timescale in seconds
        self.symbol_table = defaultdict(list)
        self.value_table = defaultdict(list)
        self.time = 0
        
    def store_pickle(self, pickle_file):
        pickle.dump({
            'symbol_table': self.symbol_table,
            'value_table': self.value_table,
            'timescale': self.timescale,
            'time': self.time
        }, open(pickle_file, "wb"))
    
    def load_pickle(self, pickle_file):
        data = pickle.load(open(pickle_file, "rb"))
        self.symbol_table = data['symbol_table']
        self.value_table = data['value_table']
        self.timescale = data['timescale']
        self.time = data['time']

    def add_signal(self, type, size, id, name):
        signal_path = '.'.join(self.current_scope + [name])
        self.symbol_table[id].append(Signal(type, size, id, signal_path))
        self.value_table[id] = [] # default value is an empty list because the initial values are set in the VCD file at least in Icarus
    
    def change_value(self, id, value):
        self.value_table[id].append(Value(value, self.time))

    def process_token(self, token):
        match token.kind:
            case pyvcd.TokenKind.COMMENT: #TODO: Does this do anything?
                pass
            case pyvcd.TokenKind.DATE: #TODO: Does this do anything?
                pass
            case pyvcd.TokenKind.ENDDEFINITIONS: #TODO: Does this do anything?
                pass
            case pyvcd.TokenKind.SCOPE:
                scope = token.scope
                match scope.type_:
                    case pyvcd.ScopeType.module:
                        self.current_scope.append(scope.ident)
                    case pyvcd.ScopeType.task:
                        print(f"Task scope: {scope.ident}")
                        raise NotImplementedError("Task scope handling not implemented.")
                    case pyvcd.ScopeType.fork:
                        print(f"Fork scope: {scope.ident}")
                        raise NotImplementedError("Fork scope handling not implemented.")
                    case pyvcd.ScopeType.function:
                        print(f"Function scope: {scope.ident}")
                        raise NotImplementedError("Function scope handling not implemented.")
                    case pyvcd.ScopeType.begin:
                        print(f"Begin scope: {scope.ident}")
                        raise NotImplementedError("Begin scope handling not implemented.")
                    case _:
                        raise ValueError(f"Unknown scope type: {scope.type}")
            case pyvcd.TokenKind.TIMESCALE:
                match token.timescale.unit:
                    case pyvcd.TimescaleUnit.second:
                        multiplier = 1
                    case pyvcd.TimescaleUnit.millisecond:
                        multiplier = 1e-3
                    case pyvcd.TimescaleUnit.microsecond:
                        multiplier = 1e-6
                    case pyvcd.TimescaleUnit.nanosecond:
                        multiplier = 1e-9
                    case pyvcd.TimescaleUnit.picosecond:
                        multiplier = 1e-12
                    case pyvcd.TimescaleUnit.femtosecond:
                        multiplier = 1e-15
                    case _:
                        raise ValueError(f"Unknown timescale unit: {token.timescale.unit}")
                match token.timescale.magnitude:
                    case pyvcd.TimescaleMagnitude.one:
                        value = 1
                    case pyvcd.TimescaleMagnitude.ten:
                        value = 10
                    case pyvcd.TimescaleMagnitude.hundred:
                        value = 100
                    case _:
                        raise ValueError(f"Unknown timescale magnitude: {token.timescale.magnitude}")
                self.timescale = multiplier * value
                print(f"Timescale set to {self.timescale} seconds.")
            case pyvcd.TokenKind.UPSCOPE:
                if self.current_scope:
                    self.current_scope.pop()
                else:
                    raise ValueError("No current scope to pop.")
            case pyvcd.TokenKind.VAR:
                type_ = str(token.var.type_)
                size = token.var.size
                identifier = token.var.id_code
                reference = token.var.reference
                self.add_signal(type_, size, identifier, reference)
            case pyvcd.TokenKind.VERSION: #TODO: Does this do anything?
                print(token.version)
                pass
            case pyvcd.TokenKind.DUMPALL: #TODO: Does this do anything?
                pass
            case pyvcd.TokenKind.DUMPOFF: #TODO: Does this do anything?
                pass
            case pyvcd.TokenKind.DUMPON: #TODO: Does this do anything?
                pass
            case pyvcd.TokenKind.DUMPVARS: #TODO: Does this do anything?
                pass
            case pyvcd.TokenKind.END:
                pass
            case pyvcd.TokenKind.CHANGE_TIME:
                self.time = token.time_change
            case pyvcd.TokenKind.CHANGE_SCALAR:
                id_code = token.scalar_change.id_code
                value = token.scalar_change.value
                if type(value) is not str:
                    # convert to binary string representation
                    value = bin(value)[2:].zfill(self.symbol_table[id_code][0].size)
                assert(type(value) is str)
                assert (len(value) == self.symbol_table[id_code][0].size or len(value) == 1), \
                    f"Value length mismatch: value='{value}', len(value)={len(value)}, expected size={self.symbol_table[id_code][0].size}, id_code={id_code}, signal={self.symbol_table[id_code][0].name}"
                self.value_table[id_code].append(Value(value, self.time))
            case pyvcd.TokenKind.CHANGE_VECTOR:
                id_code = token.vector_change.id_code
                value = token.vector_change.value
                # print(f"Processing vector change for {id_code}, {self.symbol_table[id_code][0].name}, size: {self.symbol_table[id_code][0].size} with value {value} at time {self.time}.")
                if type(value) is not str:
                    # convert to binary string representation
                    value = bin(value)[2:].zfill(self.symbol_table[id_code][0].size)
                # this fix is necessary because if there is an x in the value, it will come as a string and might not match the size
                if len(value) != self.symbol_table[id_code][0].size:
                    # extend the value with 'x' to match the size
                    # extending 'x' is really inconsequential because it will be converted to 0 in the PWL
                    if value[0] == 'x':
                        value = 'x' * (self.symbol_table[id_code][0].size - len(value)) + value
                    # extend the value with '0' to match the size
                    elif value[0] == '0' or value[0] == '1':
                        value = '0' * (self.symbol_table[id_code][0].size - len(value)) + value
                # assertions for sanity
                assert(type(value) is str)
                assert (len(value) == self.symbol_table[id_code][0].size or len(value) == 1), \
                    f"Value length mismatch: value='{value}', len(value)={len(value)}, expected size={self.symbol_table[id_code][0].size}, id_code={id_code}, signal={self.symbol_table[id_code][0].name}"
                self.value_table[id_code].append(Value(value, self.time))
            case pyvcd.TokenKind.CHANGE_REAL: #TODO: Not seen in Icarus VCD
                print(token.change_real)
                raise NotImplementedError("CHANGE_REAL token handling not implemented.")
            case pyvcd.TokenKind.CHANGE_STRING: #TODO: Not seen in Icarus VCD
                print(token.change_string)
                raise NotImplementedError("CHANGE_STRING token handling not implemented.")
            case _:
                raise ValueError(f"Unknown token kind: {token.kind}")

    def token_iterator(self, tokens):
        token = next(tokens)
        while token:
            try:
                token = next(tokens)
                self.process_token(token)
            except StopIteration:
                break
        print("Finished processing tokens.")
        # print(self.value_table)
    
    def read_vcd_file(self, vcd_file_name):
        with open(vcd_file_name, "rb") as vcd_file:
            tokens = pyvcd.tokenize(vcd_file)
            self.token_iterator(tokens)
   
    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert VCD to PWL format.")
    parser.add_argument("--vcd_file", default=None, help="Path to the VCD file to convert.")
    parser.add_argument("--output_dir", default="pyvcd_pwls", help="Directory to save PWL files.")
    parser.add_argument("--pickle_file", default="pyvcd.pickle", help="File to save the VCD data as a pickle.")
    args = parser.parse_args()


    v = VCDIngest()
    # Read the VCD file and process it
    if args.vcd_file:
        v.read_vcd_file(args.vcd_file)
        v.store_pickle(args.pickle_file)
    
    if not args.vcd_file and args.pickle_file:
        v.load_pickle(args.pickle_file)
        print("Loaded VCD data from pickle file.")

    p = PWLConverter(v)
    p.convert()
    print("Conversion to PWL format completed.")
    print("Dumping PWL files...")
    p.dump_pwls(args.output_dir)
    print("PWL files dumped successfully.")