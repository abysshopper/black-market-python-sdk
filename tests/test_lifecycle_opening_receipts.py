"""Actual event/price observations, shared with Node lifecycle-opening-buys.json.

Only curve-affecting fields are extracted. Other ABI fields use canonical test
settings; these reconstructed plans are not the original signed launch plans.
Native observations below are public historical receipts, not simulated success.
The two depth cases are actual isolated-fork receipts, not Nitro admission proof.
"""
from dataclasses import replace

import pytest

from black_market_sdk import (
    LifecycleAbyssMarketConfig, LifecycleAbyssPosition, LifecycleInitialBuy,
    LifecycleMarketConfig, LifecycleV4Position, compute_lifecycle_opening_buys,
    decode_lifecycle_pool_bound_v4_market_config, encode_lifecycle_abyss_market_config,
    encode_lifecycle_pool_bound_v4_market_config, get_known_lifecycle_profile,
    is_hmm_profile,
)
from test_hmm import hmm_config
from test_lifecycle_opening_buys import CREATOR, QUOTE, curve_plan
from test_lifecycle_read_reduction import ordinary_bound_draft

ABYSS_0 = "0x49b2ed65e2021247951193dd2c671b40867b6e064af65a2d98a70f57556412fc"
ABYSS_2 = "0x49b3bd30fe302526d7799f2ef15f3b2b261b5c44e075aa48b98c6b37148b7d59"
ABYSS_3 = "0xe1bd95e086469e6afd9791ccc957e4d22b735012cb3db4a747f703673c62c306"
HMM_DYNAMIC = "0x8e500f203ace505072e209c5f077e11de6d071ac7f298cd75c0e05c6a55c36e6"
V4_DYNAMIC = "0xf18140c2fb66a4202a0f22d62d6288bf7976db108de5381e5497aaed332a06f8"
DEPTH_LIQUIDITY = (
    1057421869054324107120425, 1089623524941071380781721, 1122805817480222911776409,
    1156998609988355360951991, 1192232675209277964411510, 1228539723008809475866314,
    1265952428912943829287789, 1304504463515088228753690, 1344230522778839518012269,
    1385166359263570646586622, 1427348814300929557562362, 1470815851151208621982673,
    1515606589169424606216455, 1561761339011857875423240, 1609321638914735926137348,
    1658330292077711246763233, 1708831405185777794807699, 1760870428104294948811141,
    1814494194782843563268533, 1869750965404726676520929, 1926690469820048473754913,
    1985363952301460298792374, 2045824217662852878932612, 2108125678782500548029865,
    2172324405573427228183039, 2238478175445066397430031, 2306646525301629401867505,
    2376890805123979473546086, 2449274233183233934992135, 2523861952935785589856101,
    2600721091650947538617587, 79199729173037441763559,
)


def market(identity, quote, price, positions, *, profile=None, fee=0, spacing=60, hook=0, minimum=0):
    template = curve_plan()
    preset = get_known_lifecycle_profile(chain_id=4663, orchestrator=template.orchestrator, profile_id=identity)
    if profile is not None:
        config = LifecycleAbyssMarketConfig(profile, fee, bytes(32), price,
            tuple(LifecycleAbyssPosition(lower, upper, liquidity, 1) for lower, upper, liquidity in positions))
        data, version = encode_lifecycle_abyss_market_config(config), 1
    else:
        config = hmm_config(identity) if is_hmm_profile(identity) else decode_lifecycle_pool_bound_v4_market_config(
            ordinary_bound_draft(1).markets[0].config)
        config = replace(config, profile_id=identity, lp_fee_pips=fee, tick_spacing=spacing,
            sqrt_price_x96=price, hook_fee_pips=hook, minimum_hook_fee_pips=minimum,
            positions=tuple(LifecycleV4Position(lower, upper, liquidity, bytes(32), 1)
                for lower, upper, liquidity in positions))
        data, version = encode_lifecycle_pool_bound_v4_market_config(config), 6
    return LifecycleMarketConfig(preset.profile.registration.adapter_id, identity, quote, len(positions), version, data)


def assert_receipts(predicted, markets, buys, expected):
    plan = replace(curve_plan(), markets=tuple(markets),
        buys=tuple(LifecycleInitialBuy(index, amount, 1, CREATOR, limit) for index, amount, limit in buys))
    quotes = compute_lifecycle_opening_buys(plan, predicted_token=predicted)
    assert [(quote.quote_spent, quote.token_out, quote.sqrt_price_after_x96) for quote in quotes] == expected


@pytest.mark.parametrize("descending", [False, True])
def test_32_band_actual_fork_receipts_and_ordered_prefix(descending):
    # Source block100115534 / 0x0d5bab455e8513101367cf71a06ad1e3c24370f6e8efb8ef873fdf75be96c3bb.
    # Actual activations: c49ca2df...106fa5 (one traversal), ed102359...3e96 (64 ordered buys).
    positions = []
    for index, liquidity in enumerate(DEPTH_LIQUIDITY):
        lower, upper = index * 600, (index + 1) * 600 if index < 31 else 887220
        positions.append((-upper, -lower, liquidity) if descending else (lower, upper, liquidity))
    pool = market(ABYSS_2 if descending else ABYSS_0, QUOTE, 1 << 96, positions,
        profile=2 if descending else 0, fee=3000)
    if descending:
        assert_receipts("0x6307442F310097Fe5328EE304FF68121566ea6A8", [pool],
            [(0, 10**15, 4295128740)] * 3, [
                (10**15, 996999999059969, 79228162439563331056874170545),
                (10**15, 996999997179907, 79228162364862324661069461841),
                (10**15, 996999995299846, 79228162290161318406129823826)])
    else:
        assert_receipts("0x066a86c02b8839145f2acA6d0E6d4D82F93F1939", [pool],
            [(0, 10**24, 1461446703485210103287273052203988822378723970341)],
            [(10**24, 557150553801046194287274, 135247353962970828062201422875)])


def test_native_hmm_overlap_and_both_zero_sentinel_directions():
    # tx0xf05e5d9b1ed8f6b3a64436dbbd75d81ab6fa902b9139d369074f4f86fe98fe98,
    # L2 block92358097 / 0x78a4afa91fdde1c38ccabc693f95212fbc415316f5c50ba0124f931804ba3348.
    first = market(HMM_DYNAMIC, "0xfF080c8ce2E5feadaCa0Da81314Ae59D232d4afD",
        10232151002651148900744889, [
            (-179100, -56880, 11649160814828374515301), (-161640, -56880, 27974557644828921750453),
            (-144180, -56880, 67472787219241676619344), (-126720, -56880, 164481821725833810436161),
            (-109260, -56880, 411791029432130484102876), (-91800, -56880, 1107148512283858901148333),
            (-74340, -56880, 3757637522353613153032911), (-56880, 887220, 4074049355853433848905949)],
        hook=150000, minimum=500)
    second = market(ABYSS_3, "0x15f3385625D7e364C5a6216FBbceadf10fa90e7d",
        93561924245275980297632694000, [
            (-118740, 3326, 16367728281405292736546758), (-118740, -14112, 39263335107905409364507875),
            (-118740, -31550, 94599840465455087221746163), (-118740, -48988, 230372823856725543471599812),
            (-118740, -66426, 576189375813054039021215410), (-118740, -83864, 1547768093687490885217429000),
            (-118740, -101302, 5249017209599276044776900793), (-887272, -118740, 5680266166353123501846520563)],
        profile=3, fee=100)
    third = market(HMM_DYNAMIC, "0x813D909Bec3d16C10a6e1E5e0a6725C332F735ba",
        248681751054113590959153502935541, [
            (38820, 161040, 6157950162987800764803), (38820, 143580, 14787840475959972133101),
            (38820, 126120, 35667295495232795423562), (38820, 108660, 86947967927082925157172),
            (38820, 91200, 217679940822918547867223), (38820, 73740, 585258068803691354760357),
            (38820, 56280, 1986352919411338449963313), (-887220, 38820, 2153613748980390626646530)],
        hook=150000, minimum=100)
    assert_receipts("0xE94b96B151747f07325422508aec5212De48Ed4e", [first, second, third], [
        (0, 18754801081897411, 0), (1, 169614413946756570525203, 4295128740), (2, 28773458743348720, 0)], [
        (18754801081897411, 1110050486286657738137774, 10359642201153169911899750),
        (169614413946756570525203, 233655783578372503617505, 92430910944256318673403630377),
        (28773458743348720, 279353875203254350853913, 245087585197328822385040934201919)])


def test_native_gapped_abyss_and_v4_opening_hook_fee():
    # tx0xbcdc2f0ec5e72ba5f2b91cb90ee349835c251ea8f5b241cd18a786f42f959699,
    # L2 block83056818 / 0xa9cfc69bc3308807e2d19604f4fc32f4c8653b208e71ac84bb7ba63b60e1ccf1.
    first = market(ABYSS_3, "0x2E8c31162b855A2ffa90F6F8634643Ad6F111e18",
        11755562826496067164730007768450, [
            (99000, 100000, 2086778719886486029134343), (96600, 98400, 2481297156765781293109651),
            (93200, 95800, 4890496397751743640066900), (88800, 92200, 9070436414772155398834614),
            (-887200, 87600, 3025536582792661775764297)], profile=3, fee=10000)
    second = market(V4_DYNAMIC, "0xED44A1B434d59B8c4F162D67A43eB198d4C64CB6",
        5167909805418990784448894947, [
            (-54600, -51400, 24507547622339078547913904), (-51400, -48600, 45586554949908613188110626),
            (-48600, -46000, 72229984496063812760771701), (-46000, -43800, 117635649898834296181144155),
            (-43800, 887200, 16168144534667510143366590)], spacing=200, hook=10000)
    assert_receipts("0xaF91716eCbe14e5096806324C1e10AB03B400A62", [first, second], [
        (0, 114783886055963920, 4295128740), (1, 10473478839643557098, 0)], [
        (114783886055963920, 2501729188952751774853, 11755467844021471195508118668106),
        (10473478839643557098, 2436983866192829913376, 5167943325563536467567172445)])
