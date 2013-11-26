# Copyright 2011 Dan Smith <dsmith@danplanet.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Baofeng UV3r radio management module"""

import time
import os
from chirp import util, chirp_common, bitwise, errors, directory
from chirp.wouxun_common import do_download, do_upload
from chirp.settings import RadioSetting, RadioSettingGroup, \
                RadioSettingValueBoolean, RadioSettingValueList, \
                RadioSettingValueInteger, RadioSettingValueString, \
                RadioSettingValueFloat

if os.getenv("CHIRP_DEBUG"):
    DEBUG = True
else:
    DEBUG = False

def _uv3r_prep(radio):
    radio.pipe.write("\x05PROGRAM")
    ack = radio.pipe.read(1)
    if ack != "\x06":
        raise errors.RadioError("Radio did not ACK first command")

    radio.pipe.write("\x02")
    ident = radio.pipe.read(8)
    if len(ident) != 8:
        print util.hexprint(ident)
        raise errors.RadioError("Radio did not send identification")

    radio.pipe.write("\x06")
    if radio.pipe.read(1) != "\x06":
        raise errors.RadioError("Radio did not ACK ident")

def uv3r_prep(radio):
    """Do the UV3R identification dance"""
    for _i in range(0, 10):
        try:
            return _uv3r_prep(radio)
        except errors.RadioError, e:
            time.sleep(1)

    raise e

def uv3r_download(radio):
    """Talk to a UV3R and do a download"""
    try:
        uv3r_prep(radio)
        return do_download(radio, 0x0000, 0x0E40, 0x0010)
    except errors.RadioError:
        raise
    except Exception, e:
        raise errors.RadioError("Failed to communicate with radio: %s" % e)

def uv3r_upload(radio):
    """Talk to a UV3R and do an upload"""
    try:
        uv3r_prep(radio)
        return do_upload(radio, 0x0000, 0x0E40, 0x0010)
    except errors.RadioError:
        raise
    except Exception, e:
        raise errors.RadioError("Failed to communicate with radio: %s" % e)

UV3R_MEM_FORMAT = """
#seekto 0x0010;
struct {
  lbcd rx_freq[4];
  u8 rxtone;
  lbcd offset[4];
  u8 txtone;
  u8 ishighpower:1,
     iswide:1,
     dtcsinvt:1,
     unknown1:1,
     dtcsinvr:1,
     unknown2:1,
     duplex:2;
  u8 unknown;
  lbcd tx_freq[4];
} tx_memory[99];

#seekto 0x0780;
struct {
  lbcd lower_vhf[2];
  lbcd upper_vhf[2];
  lbcd lower_uhf[2];
  lbcd upper_uhf[2];
} limits;

#seekto 0x07C2;
struct {
  u8 squelch;
  u8 vox;
  u8 timeout;
  u8 save:1,
     unknown_1:1,
     dw:1,
     ste:1,
     beep:1,
     unknown_2:1,
     bclo:1,
     ch_flag:1;
  u8 backlight:2,
     relaym:1,
     scanm:1,
     pri:1,
     unknown_3:3;
  u8 unknown_4[3];
  u8 pri_ch;
} settings;

#seekto 0x0810;
struct {
  lbcd rx_freq[4];
  u8 rxtone;
  lbcd offset[4];
  u8 txtone;
  u8 ishighpower:1,
     iswide:1,
     dtcsinvt:1,
     unknown1:1,
     dtcsinvr:1,
     unknown2:1,
     duplex:2;
  u8 unknown;
  lbcd tx_freq[4];
} rx_memory[99];

#seekto 0x1008;
struct {
  u8 unknown[8];
  u8 name[6];
  u8 pad[2];
} names[128];
"""

STEPS = [5.0, 6.25, 10.0, 12.5, 20.0, 25.0]
STEP_LIST = [str(x) for x in STEPS]
BACKLIGHT_LIST = ["Off", "Key", "Continuous"]
TIMEOUT_LIST = ["Off"] + ["%s sec" % x for x in range(30, 210, 30)]
SCANM_LIST = ["TO", "CO"]
PRI_CH_LIST = ["Off"] + ["%s" % x for x in range(1, 100)]
CH_FLAG_LIST = ["Freq Mode", "Channel Mode"]
POWER_LIST = ["Low", "High"]
BANDWIDTH_LIST = ["Narrow", "Wide"]
DUPLEX_LIST = ["Off", "-", "+"]
STE_LIST = ["On", "Off"]

UV3R_DUPLEX = ["", "-", "+", ""]
UV3R_POWER_LEVELS = [chirp_common.PowerLevel("High", watts=2.00),
                     chirp_common.PowerLevel("Low", watts=0.50)]
UV3R_DTCS_POL = ["NN", "NR", "RN", "RR"]

@directory.register
class UV3RRadio(chirp_common.CloneModeRadio):
    """Baofeng UV-3R"""
    VENDOR = "Baofeng"
    MODEL = "UV-3R"

    def get_features(self):
        rf = chirp_common.RadioFeatures()
        rf.has_settings = True
        rf.valid_tmodes = ["", "Tone", "TSQL", "DTCS", "Cross"]
        rf.valid_modes = ["FM", "NFM"]
        rf.valid_power_levels = UV3R_POWER_LEVELS
        rf.valid_bands = [(136000000, 235000000), (400000000, 529000000)]
        rf.valid_skips = []
        rf.valid_duplexes = ["", "-", "+", "split"]
        rf.valid_cross_modes = ["Tone->Tone", "Tone->DTCS", "DTCS->Tone",
                                "->Tone", "->DTCS"]
        rf.has_ctone = True
        rf.has_cross = True
        rf.has_tuning_step = False
        rf.has_bank = False
        rf.has_name = False
        rf.can_odd_split = True
        rf.memory_bounds = (1, 99)
        return rf

    def sync_in(self):
        self._mmap = uv3r_download(self)
        self.process_mmap()

    def sync_out(self):
        uv3r_upload(self)

    def process_mmap(self):
        self._memobj = bitwise.parse(UV3R_MEM_FORMAT, self._mmap)

    def get_memory(self, number):
        _mem = self._memobj.rx_memory[number - 1]
        mem = chirp_common.Memory()
        mem.number = number

        if _mem.get_raw()[0] == "\xff":
            mem.empty = True
            return mem

        mem.freq = int(_mem.rx_freq) * 10
        mem.offset = int(_mem.offset) * 10
        mem.duplex = UV3R_DUPLEX[_mem.duplex]
        if mem.offset > 60000000:
            if mem.duplex == "+":
                mem.offset = mem.freq + mem.offset
            elif mem.duplex == "-":
                mem.offset = mem.freq - mem.offset
            mem.duplex = "split"
        mem.power = UV3R_POWER_LEVELS[1 - _mem.ishighpower]
        if not _mem.iswide:
            mem.mode = "NFM"

        dtcspol = (int(_mem.dtcsinvt) << 1) + _mem.dtcsinvr
        mem.dtcs_polarity = UV3R_DTCS_POL[dtcspol]

        if _mem.txtone in [0, 0xFF]:
            txmode = ""
        elif _mem.txtone < 0x33:
            mem.rtone = chirp_common.TONES[_mem.txtone - 1]
            txmode = "Tone"
        elif _mem.txtone >= 0x33:
            tcode = chirp_common.DTCS_CODES[_mem.txtone - 0x33]
            mem.dtcs = tcode
            txmode = "DTCS"
        else:
            print "Bug: tx_mode is %02x" % _mem.txtone

        if _mem.rxtone in [0, 0xFF]:
            rxmode = ""
        elif _mem.rxtone < 0x33:
            mem.ctone = chirp_common.TONES[_mem.rxtone - 1]
            rxmode = "Tone"
        elif _mem.rxtone >= 0x33:
            rcode = chirp_common.DTCS_CODES[_mem.rxtone - 0x33]
            mem.dtcs = rcode
            rxmode = "DTCS"
        else:
            print "Bug: rx_mode is %02x" % _mem.rxtone

        if txmode == "Tone" and not rxmode:
            mem.tmode = "Tone"
        elif txmode == rxmode and txmode == "Tone" and mem.rtone == mem.ctone:
            mem.tmode = "TSQL"
        elif txmode == rxmode and txmode == "DTCS":
            mem.tmode = "DTCS"
        elif rxmode or txmode:
            mem.tmode = "Cross"
            mem.cross_mode = "%s->%s" % (txmode, rxmode)

        return mem

    def _set_tone(self, _mem, which, value, mode):
        if mode == "Tone":
            val = chirp_common.TONES.index(value) + 1
        elif mode == "DTCS":
            val = chirp_common.DTCS_CODES.index(value) + 0x33
        elif mode == "":
            val = 0
        else:
            raise errors.RadioError("Internal error: tmode %s" % mode)

        setattr(_mem, which, val)

    def _set_memory(self, mem, _mem):
        if mem.empty:
            _mem.set_raw("\xff" * 16)
            return

        _mem.rx_freq = mem.freq / 10
        if mem.duplex == "split":
            diff = mem.freq - mem.offset
            _mem.offset = abs(diff) / 10
            _mem.duplex = UV3R_DUPLEX.index(diff < 0 and "+" or "-")
            for i in range(0, 4):
                _mem.tx_freq[i].set_raw("\xFF")
        else:
            _mem.offset = mem.offset / 10
            _mem.duplex = UV3R_DUPLEX.index(mem.duplex)
            _mem.tx_freq = (mem.freq + mem.offset) / 10

        _mem.ishighpower = mem.power == UV3R_POWER_LEVELS[0]
        _mem.iswide = mem.mode == "FM"

        _mem.dtcsinvt = mem.dtcs_polarity[0] == "R"
        _mem.dtcsinvr = mem.dtcs_polarity[1] == "R"

        rxtone = txtone = 0
        rxmode = txmode = ""

        if mem.tmode == "DTCS":
            rxmode = txmode = "DTCS"
            rxtone = txtone = mem.dtcs
        elif mem.tmode and mem.tmode != "Cross":
            rxtone = txtone = mem.tmode == "Tone" and mem.rtone or mem.ctone
            txmode = "Tone"
            rxmode = mem.tmode == "TSQL" and "Tone" or ""
        elif mem.tmode == "Cross":
            txmode, rxmode = mem.cross_mode.split("->", 1)

            if txmode == "DTCS":
                txtone = mem.dtcs
            elif txmode == "Tone":
                txtone = mem.rtone

            if rxmode == "DTCS":
                rxtone = mem.dtcs
            elif rxmode == "Tone":
                rxtone = mem.ctone

        self._set_tone(_mem, "txtone", txtone, txmode)
        self._set_tone(_mem, "rxtone", rxtone, rxmode)

    def set_memory(self, mem):
        _tmem = self._memobj.tx_memory[mem.number - 1]
        _rmem = self._memobj.rx_memory[mem.number - 1]

        self._set_memory(mem, _tmem)
        self._set_memory(mem, _rmem)

    def get_settings(self):
        basic = RadioSettingGroup("basic", "Basic Settings")
        group = RadioSettingGroup("top", "All Settings", basic)

        rs = RadioSetting("squelch", "Squelch Level",
                          RadioSettingValueInteger(0, 9, self._memobj.settings.squelch))
        basic.append(rs)

        rs = RadioSetting("backlight", "LCD Back Light",
                          RadioSettingValueList(BACKLIGHT_LIST,
                                        BACKLIGHT_LIST[self._memobj.settings.backlight]))
        basic.append(rs)

        rs = RadioSetting("beep", "Keypad Beep",
                          RadioSettingValueBoolean(self._memobj.settings.beep))
        basic.append(rs)

        rs = RadioSetting("vox", "VOX Level (0=OFF)",
                          RadioSettingValueInteger(0, 9, self._memobj.settings.vox))
        basic.append(rs)

        rs = RadioSetting("dw", "Dual Watch",
                          RadioSettingValueBoolean(self._memobj.settings.dw))
        basic.append(rs)

        rs = RadioSetting("ste", "Squelch Tail Eliminate",
                          RadioSettingValueList(STE_LIST,
                                        STE_LIST[self._memobj.settings.ste]))
        basic.append(rs)

        rs = RadioSetting("save", "Battery Saver",
                          RadioSettingValueBoolean(self._memobj.settings.save))
        basic.append(rs)

        rs = RadioSetting("timeout", "Time Out Timer",
                          RadioSettingValueList(TIMEOUT_LIST,
                                        TIMEOUT_LIST[self._memobj.settings.timeout]))
        basic.append(rs)

        rs = RadioSetting("scanm", "Scan Mode",
                          RadioSettingValueList(SCANM_LIST,
                                        SCANM_LIST[self._memobj.settings.scanm]))
        basic.append(rs)

        rs = RadioSetting("relaym", "Repeater Sound Response",
                          RadioSettingValueBoolean(self._memobj.settings.relaym))
        basic.append(rs)

        rs = RadioSetting("bclo", "Busy Channel Lock Out",
                          RadioSettingValueBoolean(self._memobj.settings.bclo))
        basic.append(rs)

        rs = RadioSetting("pri", "Priority Channel Scanning",
                          RadioSettingValueBoolean(self._memobj.settings.pri))
        basic.append(rs)

        rs = RadioSetting("pri_ch", "Priority Channel",
                          RadioSettingValueList(PRI_CH_LIST,
                                        PRI_CH_LIST[self._memobj.settings.pri_ch]))
        basic.append(rs)

        rs = RadioSetting("ch_flag", "Display Mode",
                          RadioSettingValueList(CH_FLAG_LIST,
                                        CH_FLAG_LIST[self._memobj.settings.ch_flag]))
        basic.append(rs)

        _limit = int(self._memobj.limits.lower_vhf) / 10
        rs = RadioSetting("limits.lower_vhf", "VHF Lower Limit (115-239 MHz)",
                          RadioSettingValueInteger(115, 235, _limit))
        def apply_limit(setting, obj):
            value = int(setting.value) * 10
            obj.lower_vhf = value
        rs.set_apply_callback(apply_limit, self._memobj.limits)
        basic.append(rs)

        _limit = int(self._memobj.limits.upper_vhf) / 10
        rs = RadioSetting("limits.upper_vhf", "VHF Upper Limit (115-239 MHz)",
                          RadioSettingValueInteger(115, 235, _limit))
        def apply_limit(setting, obj):
            value = int(setting.value) * 10
            obj.upper_vhf = value
        rs.set_apply_callback(apply_limit, self._memobj.limits)
        basic.append(rs)

        _limit = int(self._memobj.limits.lower_uhf) / 10
        rs = RadioSetting("limits.lower_uhf", "UHF Lower Limit (200-529 MHz)",
                          RadioSettingValueInteger(200, 529, _limit))
        def apply_limit(setting, obj):
            value = int(setting.value) * 10
            obj.lower_uhf = value
        rs.set_apply_callback(apply_limit, self._memobj.limits)
        basic.append(rs)

        _limit = int(self._memobj.limits.upper_uhf) / 10
        rs = RadioSetting("limits.upper_uhf", "UHF Upper Limit (200-529 MHz)",
                          RadioSettingValueInteger(200, 529, _limit))
        def apply_limit(setting, obj):
            value = int(setting.value) * 10
            obj.upper_uhf = value
        rs.set_apply_callback(apply_limit, self._memobj.limits)
        basic.append(rs)

        return group

    def set_settings(self, settings):
        _settings = self._memobj.settings
        for element in settings:
            if not isinstance(element, RadioSetting):
                self.set_settings(element)
                continue
            else:
                try:
                    name = element.get_name()
                    if "." in name:
                        bits = name.split(".")
                        obj = self._memobj
                        for bit in bits[:-1]:
                            if "/" in bit:
                                bit, index = bit.split("/", 1)
                                index = int(index)
                                obj = getattr(obj, bit)[index]
                            else:
                                obj = getattr(obj, bit)
                        setting = bits[-1]
                    else:
                        obj = _settings
                        setting = element.get_name()

                    if element.has_apply_callback():
                        print "Using apply callback"
                        element.run_apply_callback()
                    else:
                        print "Setting %s = %s" % (setting, element.value)
                        setattr(obj, setting, element.value)
                except Exception, e:
                    print element.get_name()
                    raise

    @classmethod
    def match_model(cls, filedata, filename):
        return len(filedata) == 3648

    def get_raw_memory(self, number):
        _rmem = self._memobj.tx_memory[number - 1]
        _tmem = self._memobj.rx_memory[number - 1]
        return repr(_rmem) + repr(_tmem)
