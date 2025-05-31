#!/usr/bin/env python

EXTRA_HDL_FILES = [TOP / 'common' / 'hdl' / 'defines' / 'support.vhd',
                   TOP / 'common' / 'hdl' / 'defines' / 'top_defines.vhd',
                   TOP / 'common' / 'hdl' /
                       'table_read_engine_client_transfer_manager.vhd',
                   TOP / 'common' / 'hdl' /
                       'table_read_engine_client_length_manager.vhd',
                   TOP / 'common' / 'hdl' / 'table_read_engine_client.vhd',
                   TOP / 'common' / 'hdl' / 'spbram.vhd',
                   EXTRA / 'top_defines_gen.vhd']
