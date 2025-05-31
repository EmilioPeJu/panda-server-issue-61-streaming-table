#!/usr/bin/env python
import cocotb
import os
import pytest

from enum import Enum
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, ReadOnly
from cocotb_tools.runner import get_runner
from pathlib import Path

from common import get_panda_path, get_extra_path
from dma_driver import DMADriver


TOP_PATH = get_panda_path()
EXTRA_PATH = get_extra_path()


class State(Enum):
    IDLE = 0
    WAIT_ENABLE = 1
    RUNNING = 2


async def wait_for_state(dut, state, timeout=1024):
    i = 0
    while dut.state.value.to_unsigned() != state.value:
        await RisingEdge(dut.clk_i)
        i += 1
        if timeout and i > timeout:
            raise TimeoutError(f'Timeout waiting for state {state.name}')


@cocotb.test()
async def builds(dut):
    cocotb.start_soon(Clock(dut.clk_i, 1, 'ns').start(start_high=False))
    await RisingEdge(dut.clk_i)


async def reset(dut):
    dut.trig_i.value = 0
    dut.enable_i.value = 0
    dut.repeats.value = 0
    dut.enable_i.value = 0
    dut.table_length.value = 0
    dut.table_length_wstb.value = 1
    await RisingEdge(dut.clk_i)
    dut.table_length_wstb.value = 0


async def setup_table(dut, address, length, more):
    dut.table_address.value = address
    dut.table_length.value = length | (1 << 31 if more else 0)
    dut.table_address_wstb.value = 1
    dut.table_length_wstb.value = 1
    await RisingEdge(dut.clk_i)
    dut.table_address_wstb.value = 0
    dut.table_length_wstb.value = 0
    await ClockCycles(dut.clk_i, 2)


@cocotb.test()
@cocotb.parametrize(
    (('repeats'), [1]),
    (('nlines'), [4096]),
)
async def one_buffer_test(dut, repeats=1, nlines=16):
    cocotb.start_soon(Clock(dut.clk_i, 1, 'ns').start(start_high=False))
    clkedge = RisingEdge(dut.clk_i)
    await reset(dut)
    dut.repeats.value = repeats
    dma_driver = DMADriver(dut)
    data = tuple(range(nlines))
    dma_driver.set_values(0, data)
    await setup_table(dut, 0, len(data), more=False)
    await ClockCycles(dut.clk_i, 8096)
    await wait_for_state(dut, State.WAIT_ENABLE)
    dut.enable_i.value = 1
    assert dut.active_o.value == 0
    await wait_for_state(dut, State.RUNNING)
    assert dut.active_o.value == 1
    await ClockCycles(dut.clk_i, 2)
    for _ in range(repeats):
        for i in range(nlines):
            dut.trig_i.value = 1
            await clkedge
            dut.trig_i.value = 0
            await clkedge
            assert dut.out_o.value.to_unsigned() == i

    assert dut.active_o.value == 0
    await ClockCycles(dut.clk_i, 4)


def test_seq():
    runner = get_runner('nvc')
    runner.build(sources=[
                     TOP_PATH / 'common' / 'hdl' / 'defines' / 'support.vhd',
                     EXTRA_PATH / 'top_defines_gen.vhd',
                     TOP_PATH / 'common' / 'hdl' / 'defines' /
                        'top_defines.vhd',
                     TOP_PATH / 'common' / 'hdl' /
                        'table_read_engine_client_transfer_manager.vhd',
                     TOP_PATH / 'common' / 'hdl' /
                        'table_read_engine_client_length_manager.vhd',
                     TOP_PATH / 'common' / 'hdl' /
                        'table_read_engine_client.vhd',
                     TOP_PATH / 'modules' / 'pgen' / 'hdl' /
                         'pgen_ring_table.vhd',
                     TOP_PATH / 'modules' / 'pgen' / 'hdl' / 'pgen.vhd',
                 ],
                 build_args=['--std=08'],
                 build_dir='sim_pgen',
                 hdl_toplevel='pgen',
                 always=True)
    runner.test(hdl_toplevel='pgen',
                test_args=['--wave=pgen.fst'],
                test_module='test_pgen')
