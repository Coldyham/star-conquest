"""evolved's weights: one flat genome in ``evolved.LAYOUT`` order.

Underscore-prefixed so `ai.load_models` skips it (it has no `decide`); evolved
loads it by path. Everything between the markers is written by
`tools/evo_train.py`; edit by hand only to set a starting point.
"""

# BEGIN WEIGHTS
# k16k13 (trained 2026-10-16), carried by name into the current layout
WEIGHTS = [
    # strike: bias rate neutral rival ask_frac ask_abs travel exposure reach_risk kill contest degree src_threat turn inc_share ship_share value_per_ship front_gain joining partial emptied heart_dist ship_ratio rival_ahead src_left race backed exchange piecemeal leader busy new_front
    0.9618, 3.6582, -0.1655, 4.3002, -2.1919, -0.1450, -0.5770, -1.1095, -2.1163,
    -0.7828, -1.9765, 1.0774, -0.3806, 1.1285, -1.2427, 0.4901, 2.1566, -1.9204,
    1.6242, 1.6069, 0.3757, -0.4146, 0.8648, 0.6293, 0.1150, 0.0000, 0.0000,
    0.0000, 0.0000, 4.7657, 1.0731, -2.4952,
    # heavy: bias rate neutral rival ask_frac ask_abs travel exposure reach_risk kill contest degree src_threat turn inc_share ship_share value_per_ship front_gain joining partial emptied heart_dist ship_ratio rival_ahead src_left race backed exchange piecemeal leader busy new_front
    2.5257, 5.2384, 1.9381, 1.9871, -1.5683, -0.4463, -0.2813, 0.1139, -0.2068,
    0.4841, 0.1671, 0.9927, -3.4932, -0.3181, 0.7040, -2.1308, 1.5891, 1.3119,
    -2.0205, -2.3824, 0.3034, -0.3449, 0.4622, -0.7768, -8.5828, 0.0000, 0.0000,
    0.0000, 0.0000, 4.3418, 0.9112, -1.8615,
    # pool: bias rate neutral rival ask_frac ask_abs travel exposure reach_risk kill contest degree src_threat turn inc_share ship_share value_per_ship front_gain joining partial emptied heart_dist ship_ratio rival_ahead src_left race backed exchange piecemeal leader busy new_front
    -1.2916, 3.8864, -0.9050, -1.6176, -0.8054, 2.0888, 2.3927, -1.4710, -0.6899,
    -1.9945, 1.1369, 0.4769, -2.9133, -2.2277, -1.1335, -2.4796, 1.0300, 1.6601,
    0.3348, -0.7145, 0.0283, -0.4174, -0.7173, -0.2818, -3.1261, 0.0000, 0.0000,
    0.0000, 0.0000, 2.1416, -3.7168, -0.9392,
    # stagger: bias rate neutral rival ask_frac ask_abs travel exposure reach_risk kill contest degree src_threat turn inc_share ship_share value_per_ship front_gain joining partial emptied heart_dist ship_ratio rival_ahead src_left race backed exchange piecemeal leader busy new_front
    0.5592, 4.0140, -1.0559, 0.2233, -2.6166, 1.0722, 0.6600, -1.0516, -0.0874,
    0.7567, -0.8883, 0.7245, -1.9083, -0.5981, -1.4467, -1.3951, -1.0837, 3.0926,
    -0.4263, 1.3215, -1.2591, -0.3846, 0.2893, -0.2100, 4.2183, 0.0000, 0.0000,
    0.0000, 0.0000, -2.0563, -1.1352, 0.6602,
    # reinforce: bias dist_gain dest_threat src_threat dest_front amount travel turn inc_share dest_best_rate enables dest_hub dest_heart_dist ship_ratio relief_in_time
    -0.4760, 3.0542, 1.9111, -0.5485, 1.0829, -0.1023, -1.5004, -0.3070, 1.1790,
    -0.8919, 1.0599, -1.0689, 0.2595, 0.2371, 7.5109,
    # hold: bias src_threat inbound front ships turn inc_share rate deficit ship_ratio pin relief_gap
    0.1742, 2.7479, 2.0096, 0.9514, -0.2815, 1.0732, 0.0873, -2.2923, 2.0257,
    -0.3803, 0.3600, 2.5044,
    # guard: guard_inbound guard_adjacent
    1.2341, -1.6049,
    # head: h0 h1 h2 h3 h4 h5 h6 h7 h8 h9 h10 h11 h12 h13 h14 h15 h16 h17 h18 h19 h20 h21 h22 h23 h24 h25 h26 h27 h28 h29 h30 h31 h32 h33 h34 h35 h36 h37 h38 h39 h40 h41 h42 h43 h44 h45 h46 h47 h48 h49 h50 h51 h52 h53 h54 h55 h56 h57 h58 h59 h60 h61 h62 h63 h64 h65 h66 h67 h68 h69 h70 h71 h72 h73 h74 h75 h76 h77 h78 h79 h80 h81 h82 h83 h84 h85 h86 h87 h88 h89 h90 h91 h92 h93 h94 h95 h96 h97 h98 h99 h100 h101 h102 h103 h104 h105 h106 h107 h108 h109 h110 h111 h112 h113 h114 h115 h116 h117 h118 h119 h120 h121 h122 h123 h124 h125 h126 h127 h128 h129 h130 h131 h132 h133 h134 h135 h136 h137 h138 h139 h140 h141 h142 h143 h144 h145 h146 h147 h148 h149 h150 h151 h152 h153 h154 h155 h156 h157 h158 h159 h160 h161 h162 h163 h164 h165 h166 h167 h168 h169 h170 h171 h172 h173 h174 h175 h176 h177 h178 h179 h180 h181 h182 h183 h184 h185 h186 h187 h188 h189 h190 h191 h192 h193 h194 h195 h196 h197 h198 h199 h200 h201 h202 h203 h204 h205 h206 h207 h208 h209 h210 h211 h212 h213 h214 h215 h216 h217 h218 h219 h220 h221 h222 h223 h224 h225 h226 h227 h228 h229 h230 h231 h232 h233 h234 h235 h236 h237 h238 h239 h240 h241 h242 h243 h244 h245 h246 h247 h248
    -1.2015, -0.8725, -0.3017, -1.9003, -0.9689, -0.8050, -1.0186, -2.6341, -0.2715,
    -0.6258, 0.9019, 0.3107, -0.3143, -0.2982, 2.1810, -0.2412, -2.2697, -0.9398,
    0.5399, 1.6408, -1.5224, 2.3109, -0.9057, 0.1346, 1.5318, 1.3441, -0.2938,
    0.6902, -0.0791, -0.7538, 0.3265, 2.2914, 1.0168, 0.4838, 0.1822, -0.8331,
    1.6065, -0.6115, -1.5171, 0.6273, -0.5739, 0.8385, -1.7055, -0.2271, -1.6719,
    0.6405, 0.5489, -1.4890, -0.9296, -0.4541, 0.5128, 1.0179, 0.3022, 3.6390,
    0.9248, -2.2989, -0.4916, 2.3663, 3.2648, -0.2727, 0.1040, -1.9586, -0.5867,
    -1.1982, 0.9698, 0.6896, 0.6436, 0.3499, -1.4010, 1.7725, -1.0681, 1.0680,
    1.3921, 0.1962, 0.4518, 0.6779, -2.2498, 3.7862, 0.0299, 1.5023, 1.0104,
    0.3333, 0.0307, 0.2119, -0.2252, 0.7717, 1.5827, 0.3755, -1.1272, 1.4756,
    -3.1171, 1.9962, -0.7418, 0.4361, 1.5367, 1.3771, 0.9542, -1.1528, -0.7672,
    -0.2054, -0.7436, -0.3907, -0.2430, 0.5935, -2.7976, -0.7830, -1.0273, 2.0309,
    0.7701, 0.0825, -0.0553, 0.0366, 0.1297, -0.1649, 0.3622, -0.5891, 0.7695,
    -0.8906, 1.0841, -2.5179, -2.2885, 1.7118, -0.7684, -0.1820, 0.3416, -1.8368,
    -0.8154, -0.4684, 0.2095, 1.0186, -1.4283, 0.6286, 1.3471, -1.6813, 0.3238,
    -2.0082, 0.8696, -0.4295, 1.0628, 0.2284, -0.1668, -0.2069, -0.8193, 1.5749,
    1.1419, 0.1073, 3.3471, -2.3367, 0.1080, -0.4057, -0.1308, 0.4937, -0.0081,
    -0.3856, 0.4351, -1.1001, 0.8148, -0.9907, 1.8206, -0.3247, 0.1599, 0.0797,
    -1.3929, -0.1589, -0.8998, -0.4402, -0.4102, 1.0385, -1.3733, -1.9182, 1.5760,
    1.5346, -1.4451, -0.1193, 0.9298, -0.4314, 0.4197, -0.5005, -0.5984, -1.2692,
    -0.2883, 0.0813, 1.4306, 0.7402, -0.1763, -0.8057, 1.2112, -0.5217, -0.2742,
    1.3948, -0.6840, 0.9111, -0.6211, -0.9852, -1.6375, 0.4887, 2.7722, -0.5776,
    0.6234, 0.9748, 0.3683, -0.0476, 0.7374, 0.7646, 0.6247, -0.2309, 1.5675,
    1.4821, 0.1752, -3.1165, 0.2072, -1.9691, -0.7644, -0.5325, -0.8109, -1.6076,
    1.7482, 0.3690, 0.3846, -2.1092, -0.1400, -0.4997, -0.4032, -0.5129, 1.4005,
    2.8796, 2.5895, -1.2212, -0.4315, -1.7751, 2.0821, -0.8153, 1.2704, 1.0920,
    -0.7738, -0.1178, 0.4497, -0.6709, 0.9449, -0.5442, -0.5469, 0.4726, -0.4197,
    -1.2128, -0.5621, -0.6172, -0.4153, 1.3489, -0.0410,
    # evacuate: bias doom ships rate dest_threat dest_front travel ship_ratio dest_rival_adj
    -0.5920, 2.4848, -0.0833, 1.7021, 0.7677, -0.8669, -3.6259, -0.0073, -0.2624,
    # recapture: bias rate neutral rival ask_frac ask_abs travel exposure reach_risk kill contest degree src_threat turn inc_share ship_share value_per_ship front_gain joining partial emptied heart_dist ship_ratio rival_ahead src_left race backed exchange piecemeal leader busy new_front
    -1.0462, -0.1071, 0.0385, 0.8406, -0.0050, -0.1994, -0.4512, 0.5180, -1.2775,
    -2.2727, -1.0250, -2.3784, -0.5594, 2.6504, -0.2787, -0.8736, -0.4418, 0.9770,
    -2.1318, 0.9536, -0.2900, 0.7912, 1.5250, -1.0211, 2.3448, -1.5126, -1.1077,
    2.2156, -1.5509, 0.0000, 0.0000, 0.0000,
]
# END WEIGHTS
