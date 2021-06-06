# coding: utf-8
import os
import webbrowser
from typing import List
from collections import OrderedDict
from datetime import datetime
import pandas as pd
import traceback
from .objects import Mark, Direction, BI, FakeBI, FX, RawBar, NewBar
from .utils.echarts_plot import kline_pro
from .signals import check_three_fd, check_five_fd, check_seven_fd, check_nine_fd, \
    check_eleven_fd, check_thirteen_fd, Signals
from .utils.ta import RSQ


def create_fake_bis(fxs: List[FX]) -> List[FakeBI]:
    """创建 fake_bis 列表

    :param fxs: 分型序列，必须顶底分型交替
    :return: fake_bis
    """
    if len(fxs) % 2 != 0:
        fxs = fxs[:-1]

    fake_bis = []
    for i in range(1, len(fxs)):
        fx1 = fxs[i-1]
        fx2 = fxs[i]
        # assert fx1.mark != fx2.mark
        if fx1.mark == Mark.D:
            fake_bi = FakeBI(symbol=fx1.symbol, sdt=fx1.dt, edt=fx2.dt, direction=Direction.Up,
                             high=fx2.high, low=fx1.low, power=round(fx2.high-fx1.low, 2))
        elif fx1.mark == Mark.G:
            fake_bi = FakeBI(symbol=fx1.symbol, sdt=fx1.dt, edt=fx2.dt, direction=Direction.Down,
                             high=fx1.high, low=fx2.low, power=round(fx1.high-fx2.low, 2))
        else:
            raise ValueError
        fake_bis.append(fake_bi)
    return fake_bis


def remove_include(k1: NewBar, k2: NewBar, k3: RawBar):
    """去除包含关系：输入三根k线，其中k1和k2为没有包含关系的K线，k3为原始K线"""
    if k1.high < k2.high:
        direction = Direction.Up
    elif k1.high > k2.high:
        direction = Direction.Down
    else:
        k4 = NewBar(symbol=k3.symbol, dt=k3.dt, open=k3.open,
                    close=k3.close, high=k3.high, low=k3.low, vol=k3.vol, elements=[k3])
        return False, k4

    # 判断 k2 和 k3 之间是否存在包含关系，有则处理
    if (k2.high <= k3.high and k2.low >= k3.low) or (k2.high >= k3.high and k2.low <= k3.low):
        if direction == Direction.Up:
            high = max(k2.high, k3.high)
            low = max(k2.low, k3.low)
            dt = k2.dt if k2.high > k3.high else k3.dt
        elif direction == Direction.Down:
            high = min(k2.high, k3.high)
            low = min(k2.low, k3.low)
            dt = k2.dt if k2.low < k3.low else k3.dt
        else:
            raise ValueError

        if k3.open > k3.close:
            open_ = high
            close = low
        else:
            open_ = low
            close = high
        vol = k2.vol + k3.vol
        # 这里有一个隐藏Bug，len(k2.elements) 在一些及其特殊的场景下会有超大的数量，具体问题还没找到；
        # 临时解决方案是直接限定len(k2.elements)<=100
        elements = [x for x in k2.elements[:100] if x.dt != k3.dt] + [k3]
        k4 = NewBar(symbol=k3.symbol, dt=dt, open=open_,
                    close=close, high=high, low=low, vol=vol, elements=elements)
        return True, k4
    else:
        k4 = NewBar(symbol=k3.symbol, dt=k3.dt, open=k3.open,
                    close=k3.close, high=k3.high, low=k3.low, vol=k3.vol, elements=[k3])
        return False, k4


def check_fx(k1: NewBar, k2: NewBar, k3: NewBar):
    """查找分型"""
    fx = None
    if k1.high < k2.high > k3.high:
        power = "强" if k3.close < k1.low else "弱"
        # 根据 k1 与 k2 是否有缺口，选择 low
        low = k1.low if k1.high > k2.low else k2.low

        # 不允许分型的 high == low
        if low == k2.high:
            low = k1.low

        fx = FX(symbol=k1.symbol, dt=k2.dt, mark=Mark.G, high=k2.high, low=low,
                fx=k2.high, elements=[k1, k2, k3], power=power)
    if k1.low > k2.low < k3.low:
        power = "强" if k3.close > k1.high else "弱"
        # 根据 k1 与 k2 是否有缺口，选择 high
        high = k1.high if k1.low < k2.high else k2.high

        # 不允许分型的 high == low
        if high == k2.low:
            high = k1.high

        fx = FX(symbol=k1.symbol, dt=k2.dt, mark=Mark.D, high=high, low=k2.low,
                fx=k2.low, elements=[k1, k2, k3], power=power)
    return fx

def check_fxs(bars: List[NewBar]) -> List[FX]:
    """输入一串无包含关系K线，查找其中所有分型"""
    fxs = []
    for i in range(1, len(bars)-1):
        fx: FX = check_fx(bars[i-1], bars[i], bars[i+1])
        if isinstance(fx, FX):
            # 这里可能隐含Bug，默认情况下，fxs本身是顶底交替的，但是对于一些特殊情况下不是这样，这是不对的。
            # 临时处理方案，强制要求fxs序列顶底交替
            if len(fxs) >= 2 and fx.mark == fxs[-1].mark:
                fxs.pop()
            fxs.append(fx)
    return fxs


def check_bi(bars: List[NewBar]):
    """输入一串无包含关系K线，查找其中的一笔"""
    fxs = check_fxs(bars)
    if len(fxs) < 2:
        return None, bars

    fx_a = fxs[0]
    try:
        if fxs[0].mark == Mark.D:
            direction = Direction.Up
            fxs_b = [x for x in fxs if x.mark == Mark.G and x.dt > fx_a.dt and x.fx > fx_a.fx]
            if not fxs_b:
                return None, bars
            fx_b = fxs_b[0]
            for fx in fxs_b:
                if fx.high >= fx_b.high:
                    fx_b = fx
        elif fxs[0].mark == Mark.G:
            direction = Direction.Down
            fxs_b = [x for x in fxs if x.mark == Mark.D and x.dt > fx_a.dt and x.fx < fx_a.fx]
            if not fxs_b:
                return None, bars
            fx_b = fxs_b[0]
            for fx in fxs_b[1:]:
                if fx.low <= fx_b.low:
                    fx_b = fx
        else:
            raise ValueError
    except:
        traceback.print_exc()
        return None, bars

    bars_a = [x for x in bars if fx_a.elements[0].dt <= x.dt <= fx_b.elements[2].dt]
    bars_b = [x for x in bars if x.dt >= fx_b.elements[0].dt]

    # 判断fx_a和fx_b价格区间是否存在包含关系
    ab_include = (fx_a.high > fx_b.high and fx_a.low < fx_b.low) or (fx_a.high < fx_b.high and fx_a.low > fx_b.low)

    # # 判断fx_b的左侧区间是否突破
    # max_b = max([x.high for x in bars_b])
    # min_b = min([x.low for x in bars_b])
    # fx_b_end = fx_b.high < max_b or fx_b.low > min_b

    if len(bars_a) >= 7 and not ab_include:
        # 计算笔的相关属性
        power_price = round(abs(fx_b.fx - fx_a.fx), 2)
        change = round((fx_b.fx - fx_a.fx) / fx_a.fx, 4)
        fxs_ = [x for x in fxs if fx_a.elements[0].dt <= x.dt <= fx_b.elements[2].dt]
        fake_bis = create_fake_bis(fxs_)

        bi = BI(symbol=fx_a.symbol, fx_a=fx_a, fx_b=fx_b, fxs=fxs_, fake_bis=fake_bis,
                direction=direction, power=power_price, high=max(fx_a.high, fx_b.high),
                low=min(fx_a.low, fx_b.low), bars=bars_a, length=len(bars_a),
                rsq=RSQ([x.close for x in bars_a[1:-1]]), change=change)

        return bi, bars_b
    else:
        return None, bars


def get_sub_span(bis: List[BI], start_dt: [datetime, str], end_dt: [datetime, str], direction: Direction) -> List[BI]:
    """获取子区间（这是进行多级别联立分析的关键步骤）

    :param bis: 笔的列表
    :param start_dt: 子区间开始时间
    :param end_dt: 子区间结束时间
    :param direction: 方向
    :return: 子区间
    """
    start_dt = pd.to_datetime(start_dt)
    end_dt = pd.to_datetime(end_dt)
    sub = []
    for bi in bis:
        if bi.fx_b.dt > start_dt > bi.fx_a.dt:
            sub.append(bi)
        elif start_dt <= bi.fx_a.dt < bi.fx_b.dt <= end_dt:
            sub.append(bi)
        elif bi.fx_a.dt < end_dt < bi.fx_b.dt:
            sub.append(bi)
        else:
            continue

    if len(sub) > 0 and sub[0].direction != direction:
        sub = sub[1:]
    if len(sub) > 0 and sub[-1].direction != direction:
        sub = sub[:-1]
    return sub


def get_sub_bis(bis: List[BI], bi: BI) -> List[BI]:
    """获取大级别笔对象对应的小级别笔走势

    :param bis: 小级别笔列表
    :param bi: 大级别笔对象
    :return:
    """
    sub_bis = get_sub_span(bis, start_dt=bi.fx_a.dt, end_dt=bi.fx_b.dt, direction=bi.direction)
    if not sub_bis:
        return []
    return sub_bis


class CZSC:
    def __init__(self, bars: List[RawBar], freq: str, max_bi_count=100):
        """

        :param bars: K线数据
        :param freq: K线级别
        :param max_bi_count: 最大保存的笔数量
            默认值为 30，仅使用内置的信号和因子，不需要调整这个参数。
            如果进行新的信号计算需要用到更多的笔，可以适当调大这个参数。
        """
        self.max_bi_count = max_bi_count
        self.bars_raw = []  # 原始K线序列
        self.bars_ubi = []  # 未完成笔的无包含K线序列
        self.bi_list: List[BI] = []
        self.symbol = bars[0].symbol
        self.freq = freq

        for bar in bars:
            self.update(bar)

        for bi in self.bi_list:
            print(bi.fx_a.dt)
        self.signals = self.get_signals()

    def __repr__(self):
        return "<CZSC for {}>".format(self.symbol)

    def __update_bi(self):
        bars_ubi = self.bars_ubi
        if len(bars_ubi) < 3:
            return

        # 查找笔
        if not self.bi_list:
            # 第一个笔的查找
            fxs = check_fxs(bars_ubi)
            if not fxs:
                return

            fx_a = fxs[0]
            fxs_a = [x for x in fxs if x.mark == fx_a.mark]
            for fx in fxs_a:
                if (fx_a.mark == Mark.D and fx.low <= fx_a.low) \
                        or (fx_a.mark == Mark.G and fx.high >= fx_a.high):
                    fx_a = fx
            bars_ubi = [x for x in bars_ubi if x.dt >= fx_a.elements[0].dt]

            bi, bars_ubi_ = check_bi(bars_ubi)
            if isinstance(bi, BI):
                self.bi_list.append(bi)
            self.bars_ubi = bars_ubi_
            return

        last_bi = self.bi_list[-1]

        # 如果上一笔被破坏，将上一笔的bars与bars_ubi进行合并
        min_low_ubi = min([x.low for x in bars_ubi[2:]])
        max_high_ubi = max([x.high for x in bars_ubi[2:]])

        if last_bi.direction == Direction.Up and max_high_ubi > last_bi.high:
            if min_low_ubi < last_bi.low and len(self.bi_list) > 2:
                bars_ubi_a = self.bi_list[-2].bars \
                             + [x for x in self.bi_list[-1].bars if x.dt > self.bi_list[-2].bars[-1].dt] \
                             + [x for x in bars_ubi if x.dt > self.bi_list[-1].bars[-1].dt]
                self.bi_list.pop(-1)
                self.bi_list.pop(-1)
            else:
                bars_ubi_a = last_bi.bars + [x for x in bars_ubi if x.dt > last_bi.bars[-1].dt]
                self.bi_list.pop(-1)
        elif last_bi.direction == Direction.Down and min_low_ubi < last_bi.low:
            if max_high_ubi > last_bi.high and len(self.bi_list) > 2:
                bars_ubi_a = self.bi_list[-2].bars \
                             + [x for x in self.bi_list[-1].bars if x.dt > self.bi_list[-2].bars[-1].dt] \
                             + [x for x in bars_ubi if x.dt > self.bi_list[-1].bars[-1].dt]
                self.bi_list.pop(-1)
                self.bi_list.pop(-1)
            else:
                bars_ubi_a = last_bi.bars + [x for x in bars_ubi if x.dt > last_bi.bars[-1].dt]
                self.bi_list.pop(-1)
        else:
            bars_ubi_a = bars_ubi

        if len(bars_ubi_a) > 300:
            print("{} - {} 未完成笔延伸超长，延伸数量: {}".format(self.symbol, self.freq, len(bars_ubi_a)))
        bi, bars_ubi_ = check_bi(bars_ubi_a)
        self.bars_ubi = bars_ubi_
        if isinstance(bi, BI):
            self.bi_list.append(bi)

    def get_signals(self):
        s = OrderedDict({"symbol": self.symbol, "dt": self.bars_raw[-1].dt, "close": self.bars_raw[-1].close})
        # 倒0，表示未确认完成笔
        # 倒1，倒数第1笔的缩写，表示第N笔
        # 倒2，倒数第2笔的缩写，表示第N-1笔
        # 倒3，倒数第3笔的缩写，表示第N-2笔
        # 以此类推
        s.update({
            "未完成笔长度": len(self.bars_ubi),
            "三K形态": Signals.Other.value,

            "倒1方向": Signals.Other.value,
            "倒1长度": 0,
            "倒1价差力度": 0,
            "倒1涨跌幅": 0,
            "倒1拟合优度": 0,
            "倒1分型数量": 0,
            "倒1内部形态": Signals.Other.value,

            "倒2方向": Signals.Other.value,
            "倒2长度": 0,
            "倒2价差力度": 0,
            "倒2涨跌幅": 0,
            "倒2拟合优度": 0,
            "倒2分型数量": 0,

            "倒3方向": Signals.Other.value,
            "倒3长度": 0,
            "倒3价差力度": 0,
            "倒3涨跌幅": 0,
            "倒3拟合优度": 0,
            "倒3分型数量": 0,

            "倒4方向": Signals.Other.value,
            "倒4长度": 0,
            "倒4价差力度": 0,
            "倒4涨跌幅": 0,
            "倒4拟合优度": 0,
            "倒4分型数量": 0,

            "倒5方向": Signals.Other.value,
            "倒5长度": 0,
            "倒5价差力度": 0,
            "倒5涨跌幅": 0,
            "倒5拟合优度": 0,
            "倒5分型数量": 0,

            "倒1表里关系": Signals.Other.value,

            "倒1三笔": Signals.Other.value,
            "倒2三笔": Signals.Other.value,
            "倒3三笔": Signals.Other.value,
            "倒4三笔": Signals.Other.value,
            "倒5三笔": Signals.Other.value,

            "倒1形态": Signals.Other.value,
            "倒2形态": Signals.Other.value,
            "倒3形态": Signals.Other.value,
            "倒4形态": Signals.Other.value,
            "倒5形态": Signals.Other.value,
            "倒6形态": Signals.Other.value,
            "倒7形态": Signals.Other.value,
        })

        if len(self.bars_ubi) >= 3:
            tri = self.bars_ubi[-3:]
            if tri[0].high > tri[1].high < tri[2].high:
                s["三K形态"] = Signals.TK1.value
            elif tri[0].high < tri[1].high < tri[2].high:
                s["三K形态"] = Signals.TK2.value
            elif tri[0].high < tri[1].high > tri[2].high:
                s["三K形态"] = Signals.TK3.value
            elif tri[0].high > tri[1].high > tri[2].high:
                s["三K形态"] = Signals.TK4.value

        # 表里关系的定义参考：http://blog.sina.com.cn/s/blog_486e105c01007wc1.html
        min_ubi = min([x.low for x in self.bars_ubi])
        max_ubi = max([x.high for x in self.bars_ubi])

        if self.bi_list:
            last_bi = self.bi_list[-1]
            if last_bi.direction == Direction.Down:
                if min_ubi < last_bi.low:
                    s['倒1表里关系'] = Signals.BD1.value
                else:
                    s['倒1表里关系'] = Signals.BD0.value
            if last_bi.direction == Direction.Up:
                if max_ubi > last_bi.high:
                    s['倒1表里关系'] = Signals.BU1.value
                else:
                    s['倒1表里关系'] = Signals.BU0.value

        if s['倒1表里关系'] in [Signals.BU0.value, Signals.BD0.value]:
            bis = self.bi_list
        else:
            bis = self.bi_list[:-1]
            if self.bi_list:
                s['未完成笔长度'] = len(self.bars_ubi) + self.bi_list[-1].length - 3

        if not bis:
            return s

        fake_bis = bis[-1].fake_bis
        d1 = [check_five_fd(fake_bis[-5:]), check_seven_fd(fake_bis[-7:]), check_nine_fd(fake_bis[-9:]),
              check_eleven_fd(fake_bis[-11:]), check_thirteen_fd(fake_bis[-13:])]
        for v in d1:
            if v != Signals.Other.value:
                s['倒1内部形态'] = v

        for i in range(1, min(6, len(bis))):
            s['倒{}方向'.format(i)] = bis[-i].direction.value
            s['倒{}长度'.format(i)] = bis[-i].length
            s['倒{}价差力度'.format(i)] = bis[-i].power
            s['倒{}涨跌幅'.format(i)] = bis[-i].change
            s['倒{}拟合优度'.format(i)] = bis[-i].rsq
            s['倒{}分型数量'.format(i)] = len(bis[-i].fxs)

        s['倒1三笔'] = check_three_fd(bis[-3:])
        s['倒2三笔'] = check_three_fd(bis[-4:-1])
        s['倒3三笔'] = check_three_fd(bis[-5:-2])
        s['倒4三笔'] = check_three_fd(bis[-6:-3])
        s['倒5三笔'] = check_three_fd(bis[-7:-4])

        d1 = [check_five_fd(bis[-5:]), check_seven_fd(bis[-7:]), check_nine_fd(bis[-9:]),
              check_eleven_fd(bis[-11:]), check_thirteen_fd(bis[-13:])]
        for v in d1:
            if v != Signals.Other.value:
                s['倒1形态'] = v

        for i in range(2, 8):
            last_i = 1 - i
            v_seq = [
                check_five_fd(bis[last_i-5:last_i]), check_seven_fd(bis[last_i-7:last_i]),
                check_nine_fd(bis[last_i-9:last_i]), check_eleven_fd(bis[last_i-11:last_i]),
                check_thirteen_fd(bis[last_i-13:last_i])
            ]
            for v in v_seq:
                if v != Signals.Other.value:
                    s[f'倒{i}形态'] = v
        return s

    def update(self, bar: RawBar):
        """更新分析结果

        :param bar: 单根K线对象
        """
        # 更新K线序列
        if not self.bars_raw or bar.dt != self.bars_raw[-1].dt:
            self.bars_raw.append(bar)
            last_bars = [bar]
        else:
            self.bars_raw[-1] = bar
            last_bars = self.bars_ubi[-1].elements
            last_bars[-1] = bar
            self.bars_ubi.pop(-1)

        # 去除包含关系
        bars_ubi = self.bars_ubi
        for bar in last_bars:
            if len(bars_ubi) < 2:
                bars_ubi.append(NewBar(symbol=bar.symbol, dt=bar.dt, open=bar.open, close=bar.close,
                                       high=bar.high, low=bar.low, vol=bar.vol, elements=[bar]))
            else:
                k1, k2 = bars_ubi[-2:]
                has_include, k3 = remove_include(k1, k2, bar)
                if has_include:
                    bars_ubi[-1] = k3
                else:
                    bars_ubi.append(k3)
        self.bars_ubi = bars_ubi

        # 更新笔
        self.__update_bi()
        self.bi_list = self.bi_list[-self.max_bi_count:]
        if self.bi_list:
            sdt = self.bi_list[0].fx_a.elements[0].dt
            s_index = 0
            for i, bar in enumerate(self.bars_raw):
                if bar.dt >= sdt:
                    s_index = i
                    break
            self.bars_raw = self.bars_raw[s_index:]
        self.signals = self.get_signals()

    def to_echarts(self, width: str = "1400px", height: str = '580px'):
        kline = [x.__dict__ for x in self.bars_raw]
        if len(self.bi_list) > 0:
            bi = [{'dt': x.fx_a.dt, "bi": x.fx_a.fx} for x in self.bi_list] + \
                 [{'dt': self.bi_list[-1].fx_b.dt, "bi": self.bi_list[-1].fx_b.fx}]
        else:
            bi = None
        chart = kline_pro(kline, bi=bi, width=width, height=height, title="{}-{}".format(self.symbol, self.freq))
        return chart

    def open_in_browser(self, width: str = "1400px", height: str = '580px'):
        """直接在浏览器中打开分析结果

        :param width: 图表宽度
        :param height: 图表高度
        :return:
        """
        file_html = os.path.join("/Users/huayuting/work/python/github.com/czsc", "temp_czsc.html")
        chart = self.to_echarts(width, height)
        chart.render(file_html)
        webbrowser.open(file_html)

