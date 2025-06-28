# ***************************************************************************
# *   Custom Plasma CNC Postprocessor                                      *
# *   Enhanced with custom command handling and plasma features            *
# ***************************************************************************

import FreeCAD
from FreeCAD import Units
import Path
import datetime
import shlex
import argparse
import Path.Post.Utils as PostUtils
import PathScripts.PathUtils as PathUtils
from builtins import open as pyopen
import math

TOOLTIP = """
Enhanced plasma postprocessor with:
- Custom command handling (M100, M101)
- Basic torch control (M07/M08)
- 2D cutting optimized for plasma
"""

now = datetime.datetime.now()

parser = argparse.ArgumentParser(prog='plasma', add_help=False)
parser.add_argument('--no-header', action='store_true', help='suppress header output')
parser.add_argument('--no-comments', action='store_true', help='suppress comment output')
parser.add_argument('--line-numbers', action='store_true', help='prefix with line numbers')
parser.add_argument('--no-show-editor', action='store_true', help='don\'t pop up editor before writing output')
parser.add_argument('--precision', default='3', help='number of digits of precision, default=3')
parser.add_argument('--preamble', help='set commands to be issued before the first command')
parser.add_argument('--postamble', help='set commands to be issued after the last command')
parser.add_argument('--inches', action='store_true', help='Convert output for US imperial mode (G20)')
parser.add_argument('--pierce-delay', default='0.5', help='Pierce delay in seconds (default: 0.5)')

OUTPUT_COMMENTS = True
OUTPUT_HEADER = True
OUTPUT_LINE_NUMBERS = False
SHOW_EDITOR = True
MODAL = True
PRECISION = 3
COMMAND_SPACE = " "
PIERCE_DELAY = 0.5

UNITS = "G21"
UNIT_SPEED_FORMAT = "mm/min"
UNIT_FORMAT = "mm"

PREAMBLE = """G90 G54 G40 G49 G80
G21
(Setup for plasma cutting)
"""

POSTAMBLE = """M8 (Torch OFF)
G0 X0 Y0 (Return home)
M30 (Program end)
"""

def processArguments(argstring):
    global OUTPUT_HEADER, OUTPUT_COMMENTS, OUTPUT_LINE_NUMBERS, SHOW_EDITOR
    global PRECISION, PREAMBLE, POSTAMBLE, UNITS, UNIT_SPEED_FORMAT, UNIT_FORMAT
    global PIERCE_DELAY

    try:
        args = parser.parse_args(shlex.split(argstring))
        if args.no_header:
            OUTPUT_HEADER = False
        if args.no_comments:
            OUTPUT_COMMENTS = False
        if args.line_numbers:
            OUTPUT_LINE_NUMBERS = True
        if args.no_show_editor:
            SHOW_EDITOR = False
        if args.precision:
            PRECISION = args.precision
        if args.preamble:
            PREAMBLE = args.preamble
        if args.postamble:
            POSTAMBLE = args.postamble
        if args.inches:
            UNITS = "G20"
            UNIT_SPEED_FORMAT = "in/min"
            UNIT_FORMAT = "in"
        if args.pierce_delay:
            PIERCE_DELAY = float(args.pierce_delay)
    except:
        return False
    return True

def export(objectslist, filename, argstring):
    if not processArguments(argstring):
        return None

    global UNITS, UNIT_FORMAT, UNIT_SPEED_FORMAT
    
    for obj in objectslist:
        if not hasattr(obj, "Path"):
            print(f"The object {obj.Name} is not a path. Please select only path and Compounds.")
            return None

    print("Postprocessing...")
    gcode = ""

    if OUTPUT_HEADER:
        gcode += f"(Exported by FreeCAD Plasma Postprocessor - {now})\n"
        gcode += f"(Units: {'inches' if UNITS == 'G20' else 'mm'})\n"
        gcode += f"(Pierce delay: {PIERCE_DELAY}s)\n"

    if OUTPUT_COMMENTS:
        gcode += "(begin preamble)\n"
    for line in PREAMBLE.splitlines(False):
        gcode += line + "\n"
    gcode += UNITS + "\n"
    
    for obj in objectslist:
        if hasattr(obj, "Active") and not obj.Active:
            continue
        if hasattr(obj, "Base") and hasattr(obj.Base, "Active") and not obj.Base.Active:
            continue

        if OUTPUT_COMMENTS:
            gcode += f"(begin operation: {obj.Label})\n"
            gcode += f"(machine units: {UNIT_SPEED_FORMAT})\n"

        gcode += parse(obj)

        if OUTPUT_COMMENTS:
            gcode += f"(finish operation: {obj.Label})\n"

    if OUTPUT_COMMENTS:
        gcode += "(begin postamble)\n"
    for line in POSTAMBLE.splitlines(True):
        gcode += line + "\n"

    if FreeCAD.GuiUp and SHOW_EDITOR:
        final = gcode
        if len(gcode) > 100000:
            print("Skipping editor since output is greater than 100kb")
        else:
            dia = PostUtils.GCodeEditorDialog()
            dia.editor.setText(gcode)
            result = dia.exec_()
            if result:
                final = dia.editor.toPlainText()
    else:
        final = gcode

    print("Postprocessing complete.")

    if filename != "-":
        with pyopen(filename, "w") as gfile:
            gfile.write(final)

    return final

def parse(pathobj):
    global PRECISION, MODAL, UNIT_FORMAT, UNIT_SPEED_FORMAT, PIERCE_DELAY

    out = ""
    lastcommand = None
    precision_string = f".{PRECISION}f"
    currLocation = {}
    cutting = False
    
    params = ["X", "Y", "F", "I", "J"]

    for c in pathobj.Path.Commands:
        outstring = []
        command = c.Name

        # --- CUSTOM COMMAND HANDLING ---
        if "CustomCommand" in command:
            param_str = " ".join([f"{k}{format(float(v), precision_string)}" for k,v in c.Parameters.items()])
            out += f"(Custom: {command} {param_str})\n"
            continue
        elif command in ["M100", "M101"]:
            out += command
            if c.Parameters:
                param_str = " ".join([f"{k}{format(float(v), precision_string)}" for k,v in c.Parameters.items()])
                out += f" {param_str}"
            out += " (Special plasma command)\n"
            continue
        # --- END CUSTOM COMMANDS ---

        # Skip unnecessary commands
        if command in ["M3", "M4", "M5", "G17", "G18", "G19", "G43"]:
            continue

        # Process movement parameters
        for param in params:
            if param in c.Parameters:
                if param == "F":
                    speed = Units.Quantity(c.Parameters["F"], FreeCAD.Units.Velocity)
                    speed_value = speed.getValueAs(UNIT_SPEED_FORMAT)
                    if speed_value > 0.0:
                        outstring.append(param + format(float(speed_value), precision_string))
                else:
                    pos = Units.Quantity(c.Parameters[param], FreeCAD.Units.Length)
                    outstring.append(param + format(float(pos.getValueAs(UNIT_FORMAT)), precision_string))

        # Torch control logic
        if command in ["G1", "G2", "G3"] and not cutting:
            out += "M7 (Torch ON)\n"
            out += f"G4 P{PIERCE_DELAY} (Pierce delay)\n"
            cutting = True
        elif command == "G0" and cutting:
            out += "M8 (Torch OFF)\n"
            cutting = False

        # Generate output line
        if outstring:
            out += command + " " + " ".join(outstring) + "\n"
        
        # Update current location
        for param in params:
            if param in c.Parameters:
                currLocation[param] = c.Parameters[param]

    # Final safety checks
    if cutting:
        out += "M8 (Torch OFF)\n"

    return out

print(__name__ + " enhanced plasma postprocessor loaded.")