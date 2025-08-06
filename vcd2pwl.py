import argparse
import os
import re
from collections import defaultdict
import vcd.reader as pyvcd
import pickle
import sys

class VCDIngest:
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
    class Value:
        def __init__(self, value, time):
            self.value = value
            self.time = time
        def __str__(self):
            return f"{self.value} @ {self.time}"

    def __init__(self):
        self.current_scope = []
        self.timescale = 1  # Default timescale in seconds
        self.symbol_table = defaultdict(list)
        self.value_table = defaultdict(list)
        self.time = 0
        
    def store_pickle(self, pickle_file):
        pass

    def add_signal(self, type, size, id, name):
        signal_path = '.'.join(self.current_scope + [name])
        # print(f"Signal added: {signal_path} (Type: {type}, Size: {size}, ID: {id})")
        self.symbol_table[id].append(self.Signal(type, size, id, signal_path))
        self.value_table[id] = [self.Value('X',self.time)] # default value
    
    def change_value(self, id, value):
        self.value_table[id].append(self.Value(value, self.time))

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
                        # print(f"Module scope: {scope.ident}")
                        # print(f"Current scope: {'.'.join(self.current_scope)}")
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
                timescale = multiplier * value
                print(f"Timescale set to {timescale} seconds.")
            case pyvcd.TokenKind.UPSCOPE:
                if self.current_scope:
                    self.current_scope.pop()
                    # print(f"Upscoped to {'.'.join(self.current_scope)}")
                else:
                    raise ValueError("No current scope to pop.")
            case pyvcd.TokenKind.VAR:
                type = str(token.var.type_)
                size = token.var.size
                identifier = token.var.id_code
                reference = token.var.reference
                self.add_signal(type, size, identifier, reference)
                # raise NotImplementedError("VAR token handling not implemented.")
            case pyvcd.TokenKind.VERSION: #TODO: Does this do anything?
                print(token.version)
                pass
            case pyvcd.TokenKind.DUMPALL: #TODO: Does this do anything?
                pass
                # raise NotImplementedError("DUMPALL token handling not implemented.")
            case pyvcd.TokenKind.DUMPOFF: #TODO: Does this do anything?
                pass
                # raise NotImplementedError("DUMPOFF token handling not implemented.")
            case pyvcd.TokenKind.DUMPON: #TODO: Does this do anything?
                pass
                # raise NotImplementedError("DUMPON token handling not implemented.")
            case pyvcd.TokenKind.DUMPVARS: #TODO: Does this do anything?
                pass
                # raise NotImplementedError("DUMPVARS token handling not implemented.")
            case pyvcd.TokenKind.END:
                # raise NotImplementedError("END token handling not implemented.")
                pass
            case pyvcd.TokenKind.CHANGE_TIME:
                self.time = token.time_change
                # print(f"Time changed to {self.time}")
                # input("Press Enter to continue...")
                # raise NotImplementedError("CHANGE_TIME token handling not implemented.")
            case pyvcd.TokenKind.CHANGE_SCALAR:
                id_code = token.scalar_change.id_code
                value = token.scalar_change.value
                self.value_table[id_code].append(self.Value(value, self.time))
                # print(f"Scalar change: {id_code} = {value}")
                # raise NotImplementedError("CHANGE_SCALAR token handling not implemented.")
            case pyvcd.TokenKind.CHANGE_VECTOR:
                id_code = token.vector_change.id_code
                value = token.vector_change.value
                self.value_table[id_code].append(self.Value(value, self.time))
                # print(f"Vector change: {id_code} = {value}")
                # raise NotImplementedError("CHANGE_VECTOR token handling not implemented.")
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


    # Ensure the output directory exists
    os.makedirs("pwls", exist_ok=True)

    v = VCDIngest()
    # Read the VCD file and process it
    if args.vcd_file:
        v.read_vcd_file(args.vcd_file)
        v.store_pickle(args.pickle_file)