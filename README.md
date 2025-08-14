# vcd2pwl

Converts VCD files to PWL files. WIP.

Dependencies:
- https://github.com/SanDisk-Open-Source/pyvcd/
- https://github.com/PyHDI/Pyverilog

# `vcd2pwl.py`

This script is able to take in a VCD file and generate analog equivalents for SPICE stimulus

# `create_stimulus.py`

This script is able to parse port definitions from equivalent SPICE and Verilog files to determine which stimulus to generate. The end goal is to have it generate SPICE stimulus which uses the correct PWL files and prints the correct outputs
