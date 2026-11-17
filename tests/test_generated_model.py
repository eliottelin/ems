import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'template_generated_app'))
from models.water_balance import step as water
from models.soil_carbon import step as carbon
from models.vegetation import step as vegetation
from weather import demo_weather
from engine import simulate,DEFAULTS

def test_water_balance_closes_and_stays_bounded():
    w=water(100,25,5,DEFAULTS['water_balance'])
    assert abs(w['water_residual'])<1e-9
    assert 0<=w['storage']<=200

def test_soil_carbon_mass_balance_closes():
    c=carbon(1000,5000,5,26,.7,DEFAULTS['soil_carbon'])
    assert abs(c['carbon_residual'])<1e-9
    assert c['respiration']>=0

def test_biomass_nonnegative():
    v=vegetation(300,24,0.8,DEFAULTS['vegetation'])
    assert v['biomass']>=0

def test_demo_daily_count():
    assert len(demo_weather(37))==37

def test_coupled_execution_three_models():
    cfg={'models':['water_balance','soil_carbon','vegetation'],'weather_source':'demo_weather', 'latitude':30.6, 'longitude':-96.3,'days':90}
    out=simulate(cfg)
    assert len(out)==90
    assert out['water_residual_mm'].abs().max()<1e-8
    assert out['carbon_residual_gC_m2'].abs().max()<1e-8
    assert (out['soil_carbon_gC_m2']>0).all()
