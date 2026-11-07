# ADC Deliverable 2: running implementation (Group 34, Sub Team 66, Part A)
# Yatukulan Vivekanandan and Krrish Railwani

Python 3.10 or newer, standard library only. Nothing to install.

    python run_panel.py          # browser panel: touchscreen, cabinet hardware, Monitoring System view
    python run_scenarios.py      # the main scenarios as a text transcript (no browser)
    python -m unittest discover -s tests -t .      # 44 automated checks
    python tools/trace.py        # every D1 requirement must be tagged in the code

The ADC software is seven modules, one class per file in adc/:

    user_interface.py   UI  screen, reader, buzzer          access_control.py  AC  credentials, roles, session
    mode_fault.py       MF  modes and faults                dispensing.py      DI  transactions and inventory
    communication.py    CG  Monitoring System link, queue   database.py        DB  storage and log
    hardware.py         HW  locks, door sensors, clock      cabinet.py   builds the modules and runs one 0.1 s step (not a module)

Other folders

    sim/    simulated drawers, devices, link and Monitoring System stand-in (not part of the ADC)
    panel/  the browser panel
    tests/  automated checks, each commented with the requirement it covers
    tools/  trace.py reads the "Implements:" lines in adc/*.py and checks them against the D1 requirement list
    docs/   module_spec.py holds the interface tables of the design document; run it to check them against the code
    data/   the D1 requirements (IDs and text) used by tools/trace.py

Link down, Monitoring System silent and Monitoring System answers inject the communication faults (silent: the link shows lost after 10 s without an acknowledgement).
Time in the panel is simulated. Use 5x, 20x, +10 s and +60 s to move past the 15 s, 30 s and 60 s limits quickly.
