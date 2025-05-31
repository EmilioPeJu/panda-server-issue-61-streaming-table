import cocotb

from cocotb.triggers import RisingEdge
from collections import deque
from pathlib import Path


class DMADriver(object):
    def __init__(self, dut, panda_src, module):
        self.dut = dut
        self.dut.dma_ack_i.value = 0
        self.dut.dma_done_i.value = 0
        self.dut.dma_data_i.value = 0
        self.dut.dma_valid_i.value = 0
        self.module = module
        self.panda_src = panda_src
        cocotb.start_soon(self.run())

    async def run(self):
        while True:
            await RisingEdge(self.dut.dma_req_o)
            await RisingEdge(self.dut.clk_i)
            self.dut.dma_ack_i.value = 1
            addr = self.dut.dma_addr_o.value.to_unsigned()
            length = self.dut.dma_len_o.value.to_unsigned()
            with open(Path(self.panda_src) / 'modules' / self.module / 'tests_assets' / 
                      f'{addr}.txt', 'r') as f:
                lines = list(f)[1:]

            data = deque([int(item[2:], 16) if item.startswith('0x') else
                          int(item) for item in lines])
            await RisingEdge(self.dut.clk_i)
            self.dut.dma_ack_i.value = 0
            for i in range(length - 1):
                self.dut.dma_data_i.value = data.popleft()
                self.dut.dma_valid_i.value = 1
                await RisingEdge(self.dut.clk_i)

            self.dut.dma_data_i.value = data.popleft()
            self.dut.dma_valid_i.value = 1
            self.dut.dma_done_i.value = 1
            await RisingEdge(self.dut.clk_i)
            self.dut.dma_done_i.value = 0
            self.dut.dma_valid_i.value = 0
