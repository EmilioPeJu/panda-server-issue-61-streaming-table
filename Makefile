TOP := $(CURDIR)
PYTHON = python
SIMULATOR = nvc
BUILD_DIR = build
MODULE = all
include CONFIG

cocotb_tests:
	$(PYTHON) $(TOP)/timing-tests/cocotb_timing_test_runner.py -c \
        --panda-src $(FPGA) \
        --panda-build-dir $(BUILD_DIR) \
        --sim $(SIMULATOR) \
        $(MODULE) \
        '$(TEST)'

dev_tests:
	panda_src_dir=$(FPGA) $(PYTHON) -m pytest dev-tests
