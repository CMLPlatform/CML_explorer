"""Keyword canonicalisation and theme assignment for the CML network dashboard.

One shared taxonomy for both departments (themes are grouped into broader areas), built in three layers:
  1. ALIASES merge spelling/abbreviation variants of the same keyword; DROP removes generic ones.
  2. KEYWORD_THEMES maps every canonical keyword to one or more themes (explicit, reviewable).
  3. TEXT_PATTERNS back-fill people with little keyword evidence from their title and bio.
Run `python3 harmonize.py` to print the resulting assignment per person for review.
"""
import json
import re
from collections import Counter
from pathlib import Path

# ---------- theme -> area (area drives the optional "Colour: area" mode) ----------
AREAS = {
    "Assessment methods": "#2457c5",
    "Resources, energy & built environment": "#d9730d",
    "Society, food & policy": "#8e44ad",
    "Biodiversity & ecosystems": "#1f8a4c",
    "Pollution & chemicals": "#c0392b",
    "Water, land & earth system": "#0984e3",
    "Modelling & data": "#5d6d7e",
}
THEME_AREA = {
    "Life cycle assessment": "Assessment methods",
    "Material & substance flow analysis": "Assessment methods",
    "Input-output analysis & footprints": "Assessment methods",
    "Sustainability assessment & indicators": "Assessment methods",
    "Decision support & uncertainty": "Assessment methods",
    "Circular economy & critical materials": "Resources, energy & built environment",
    "Climate, energy & decarbonisation": "Resources, energy & built environment",
    "Built environment & mobility": "Resources, energy & built environment",
    "Food systems": "Society, food & policy",
    "Wellbeing, sufficiency & post-growth": "Society, food & policy",
    "Policy, economics & governance": "Society, food & policy",
    "Biodiversity & community ecology": "Biodiversity & ecosystems",
    "Conservation, invasion & resilience": "Biodiversity & ecosystems",
    "Animal & movement ecology": "Biodiversity & ecosystems",
    "Plant & vegetation ecology": "Biodiversity & ecosystems",
    "Soil, fungi & microbes": "Biodiversity & ecosystems",
    "Ecosystem services & nature-based solutions": "Biodiversity & ecosystems",
    "Urban nature & green infrastructure": "Biodiversity & ecosystems",
    "Genetics & eDNA": "Biodiversity & ecosystems",
    "Ecotoxicology & chemical pollution": "Pollution & chemicals",
    "Water & aquatic systems": "Water, land & earth system",
    "Land use & landscapes": "Water, land & earth system",
    "Earth system & global change": "Water, land & earth system",
    "Nutrients & biogeochemical cycles": "Water, land & earth system",
    "Modelling, scenarios & integrated assessment": "Modelling & data",
    "Data science, computing & AI": "Modelling & data",
    "Spatial analysis & GIS": "Modelling & data",
}
T = {name: name for name in THEME_AREA}  # typo guard: use T["..."] below
LCA, MFA, IO, SUST, DEC = (T["Life cycle assessment"], T["Material & substance flow analysis"],
                           T["Input-output analysis & footprints"], T["Sustainability assessment & indicators"],
                           T["Decision support & uncertainty"])
CIRC, CLIM, BUILT = (T["Circular economy & critical materials"], T["Climate, energy & decarbonisation"],
                     T["Built environment & mobility"])
FOOD, WELL, POL = (T["Food systems"], T["Wellbeing, sufficiency & post-growth"], T["Policy, economics & governance"])
BIO, CONS, ANIM, PLANT, SOIL, ES, URB, GEN = (
    T["Biodiversity & community ecology"], T["Conservation, invasion & resilience"], T["Animal & movement ecology"],
    T["Plant & vegetation ecology"], T["Soil, fungi & microbes"], T["Ecosystem services & nature-based solutions"],
    T["Urban nature & green infrastructure"], T["Genetics & eDNA"])
TOX = T["Ecotoxicology & chemical pollution"]
WAT, LAND, EARTH, NUTR = (T["Water & aquatic systems"], T["Land use & landscapes"],
                          T["Earth system & global change"], T["Nutrients & biogeochemical cycles"])
MOD, DATA, SPAT = (T["Modelling, scenarios & integrated assessment"], T["Data science, computing & AI"],
                   T["Spatial analysis & GIS"])

# ---------- layer 1: merge variants, drop generic keywords ----------
ALIASES = {
    "life cycle assessment (lca)": "life cycle assessment", "lca": "life cycle assessment",
    "life cycle impact assessment (lcia)": "life cycle assessment",
    "plca": "prospective life cycle assessment", "social lca (s-lca)": "social life cycle assessment",
    "life cycle sustainability analysis (lcsa)": "life cycle sustainability analysis",
    "life cycle inventory modelling": "life cycle inventory", "life cycle inventory databases (ecoinvent)": "life cycle inventory",
    "ssbd": "safe and sustainable by design", "safe and sustainable-by-design (ssbd)": "safe and sustainable by design",
    "material flow accounting (mfa)": "material flow analysis", "material flow analysis (mfa)": "material flow analysis",
    "material-flow analysis": "material flow analysis", "material flow accounting": "material flow analysis",
    "material stocks and flows (mfa)": "material flow analysis", "dynamic material flow analysis": "material flow analysis",
    "substance flow analysis (sfa)": "substance flow analysis",
    "environmental input-output analysis": "input-output analysis", "input-output modeling": "input-output analysis",
    "environmental footprint": "environmental footprints", "environmental footprinting": "environmental footprints",
    "decarbonization": "decarbonisation",
    "integrated assessment": "integrated assessment models", "integrated assessment models (iams)": "integrated assessment models",
    "scenario modelling": "scenario analysis",
    "modelling of environmental systems": "environmental modelling", "simulation of environmental systems": "environmental modelling",
    "mathematical programming": "optimisation", "optimalization": "optimisation",
    "geographic information systems (gis)": "gis", "geospatial analysis": "spatial analysis",
    "global change": "global environmental change", "environmental change": "global environmental change",
    "conservation": "conservation biology",
    "insecticides": "pesticides", "neonicotinoids": "pesticides", "agrochemicals": "pesticides",
    "microplastics": "plastic pollution",
    "indicators": "sustainability indicators",
    "decision-making": "decision support", "decision support systems": "decision support",
    "uncertainty analysis": "uncertainty & sensitivity analysis", "global sensitivity analysis": "uncertainty & sensitivity analysis",
    "critical minerals": "critical raw materials", "critical materials": "critical raw materials",
    "resilent supply chains": "resilient supply chains",
    "urban nature": "urban ecology",
    "communities": "community ecology", "networks": "ecological networks",
    "environmental dna (edna)": "edna",
}
# Profile content the scraped keyword list misses (read from the bio): person -> extra keywords, run through ALIASES too.
KEYWORD_EXTRA = {
    "Martina Vijver": ["engineered nanomaterials", "mesocosm", "multiple stressors"],   # bio: nanomaterials, mesocosms, multiple stressors
}
DROP = {"sustainability", "environment", "industrial ecology", "environmental sciences", "environmental science",
        "sustainable development", "master industrial ecology", "curriculum", "principles of environmental sciences",
        "drivers", "capital", "ecology", "liveable planet"}

# ---------- layer 2: canonical keyword -> themes (empty list = kept as keyword, no theme) ----------
KEYWORD_THEMES = {
    "life cycle assessment": [LCA], "prospective life cycle assessment": [LCA], "social life cycle assessment": [LCA, SUST],
    "life cycle costing": [LCA], "life cycle sustainability analysis": [LCA, SUST], "life cycle inventory": [LCA, DATA],
    "safe and sustainable by design": [LCA, SUST], "emerging technologies": [SUST],
    "material flow analysis": [MFA], "substance flow analysis": [MFA], "socioeconomic metabolism": [MFA],
    "input-output analysis": [IO], "hybrid supply-use tables": [IO], "environmental footprints": [IO, SUST],
    "consumption-based emissions": [IO, CLIM], "trade": [IO, POL], "global food trade": [FOOD, IO], "co2": [CLIM],
    "sustainability indicators": [SUST], "quantitative sustainability assessment": [SUST], "impact assessment": [SUST],
    "planetary boundaries": [SUST, EARTH], "impact assessment of environmental policies": [POL, SUST],
    "uncertainty & sensitivity analysis": [DEC], "decision support": [DEC], "resilience": [],
    "circular economy": [CIRC], "critical raw materials": [CIRC], "resilient supply chains": [CIRC],
    "sustainable resource management": [CIRC], "consumer goods": [CIRC], "textiles": [CIRC], "reverse engineering": [CIRC, DATA],
    "concrete recycling": [CIRC, BUILT], "construction and demolition waste management": [CIRC, BUILT],
    "construction materials": [BUILT, CIRC], "built environment": [BUILT], "buildings": [BUILT], "transport": [BUILT],
    "urban design": [BUILT], "urban studies": [],   # weak: shared by several IE profiles, so no theme of its own
    "decarbonisation": [CLIM], "energy modeling": [CLIM, MOD], "energy and climate economics": [CLIM, POL],
    "policy-making": [POL], "econometrics": [POL], "global south": [POL], "environmental philosophy": [POL, ES],   # human-nature relations, worldviews
    "sustainability and religion": [POL], "beyond-gdp": [WELL, POL], "post-growth": [WELL, POL], "sufficiency": [WELL],
    "human wellbeing": [WELL, ES], "environmental health": [],
    "food": [FOOD], "sustainable food systems": [FOOD], "animal welfare": [FOOD],
    "integrated assessment models": [MOD, SUST], "scenario analysis": [MOD], "environmental modelling": [MOD],
    "optimisation": [MOD], "dynamic modeling": [MOD], "biogeochemical modelling": [NUTR, EARTH, MOD],
    "global vegetation modelling": [PLANT, MOD], "high performance computing": [DATA, MOD], "data analytics": [DATA],
    "artificial intelligence": [DATA], "python": [DATA], "computational techniques": [DATA], "data collection": [DATA],
    "gis": [SPAT], "spatial analysis": [SPAT], "spatial ecology": [SPAT, BIO], "spatial epidemiology": [SPAT],
    "earth system": [EARTH], "global environmental change": [EARTH], "atmosphere- biosphere interaction": [NUTR, EARTH],
    "nitrogen": [NUTR], "artificial fertilizer": [TOX, NUTR],
    "water": [WAT], "water use": [WAT], "water quality": [WAT], "global hydrology": [WAT, EARTH, MOD],
    "freshwater biodiversity": [WAT, BIO], "aquatic ecology": [WAT], "aquatic invertebrates": [WAT, TOX], "plankton": [WAT],
    "coastal management": [WAT, ES], "sandy coasts": [WAT, ES], "water-energy-land system": [WAT, LAND, CLIM],
    "land use change": [LAND], "african savannas": [ANIM, LAND],
    "biodiversity": [BIO], "community ecology": [BIO], "food webs": [BIO], "ecosystem functioning": [BIO],
    "ecosystem stability": [BIO, CONS], "ecological organization": [BIO], "biogeography": [BIO], "host-parasite interactions": [BIO],
    "cross-ecosystem linkages": [BIO], "plant communities": [PLANT, BIO], "trait based approaches": [PLANT, BIO],
    "ecological networks": [SOIL, BIO], "large herbivore ecology": [ANIM, BIO],
    "bird ecology": [ANIM], "biologging": [ANIM], "movement ecology": [ANIM], "wildlife tracking": [ANIM],
    "conservation biology": [CONS], "biodiversity monitoring and management": [CONS, GEN], "invasive species": [CONS, BIO],
    "insect decline": [CONS, BIO], "conservation genetics": [GEN, CONS], "hybridization": [GEN], "edna": [GEN],
    "fungi": [SOIL], "soil ecology": [SOIL], "microbiomes": [SOIL], "plant soil interactions": [SOIL, PLANT],
    "ecosystem services": [ES], "nature-based solutions": [ES], "human-nature interactions": [ES], "ecological engineering": [ES],
    "urban natural capital": [ES, URB], "urban ecology": [URB], "green infrastructures": [URB],
    "ecotoxicology": [TOX], "nanoecotoxicology": [TOX], "engineered nanomaterials": [TOX], "pesticides": [TOX], "plastic pollution": [TOX],
    "emerging contaminants": [TOX], "mesocosm": [TOX], "multiple stressors": [TOX], "environmental chemistry": [TOX],
    "ecological risk assessment": [TOX], "risk assessment": [TOX], "metals": [TOX],
}

# ---------- keywords added for the full staff list (PhD candidates, postdocs, researchers) ----------
# Same three layers as above, kept in a separate block so they can be reviewed apart from the hand-made mappings.
ALIASES.update({
    "lifecycle assessment (lca)": "life cycle assessment", "life cycle analysis": "life cycle assessment",
    "life cycle analysis (lca)": "life cycle assessment", "life cycle thinking": "life cycle assessment",
    "prospective life cycle analysis (plca)": "prospective life cycle assessment", "ex-ante lca": "prospective life cycle assessment",
    "prospective lca": "prospective life cycle assessment", "prospective (lca)": "prospective life cycle assessment",
    "social life cycle assessment (s-lca)": "social life cycle assessment",
    "life cycle sustainability assessment": "life cycle sustainability analysis",
    "material-flow analysis (mfa)": "material flow analysis", "dynamic material flow analysis (dmfa)": "material flow analysis",
    "critical raw materials (crm)": "critical raw materials", "circular economies": "circular economy",
    "geographic information system (gis)": "gis", "gis software": "gis",
    "optimization": "optimisation", "simulation and optimization": "optimisation",
    "multi regional input-output": "input-output analysis", "multi-regional input output modeling (mrio)": "input-output analysis",
    "mrio": "input-output analysis", "environmentally extended input-output analysis": "input-output analysis",
    "beyond gdp": "beyond-gdp", "well-being": "human wellbeing", "wellbeing": "human wellbeing", "inclusive wellbeing": "human wellbeing",
    "plant-soil interactions": "plant soil interactions", "behavioral ecology": "behavioural ecology",
    "climate change mitigation": "climate mitigation", "emerging technology": "emerging technologies",
    "plastics pollution": "plastic pollution", "decision support system": "decision support",
    "multi-criteria decision analysis (mcda)": "decision support", "artificial intelligence (ai)": "artificial intelligence",
    "environmental dna": "edna", "trait-based approach": "trait based approaches",
    "atmosphere- biosphere interactions": "atmosphere- biosphere interaction", "econometrics.": "econometrics",
    "resilience.": "resilience", "built environment'": "built environment", "biodiveristy conservation": "biodiversity conservation",
    "environmental system analysis": "environmental systems analysis", "climate change and sustainability": "climate change",
    "keywords computational biology": "computational biology", "food systems and dietary change": "dietary change",
})
DROP |= {"sustainable innovation & research", "programme coordination", "student affairs", "study advice",
         "industrial ecology (ie)", "centre for sustainability", "environmental biology", "interdisciplinary research",
         "inter- and transdisciplinary", "transdisciplinary research", "laboratory skills", "engineering",
         "engineering applications", "website management", "science communication", "research by design",
         "sampling methodology", "cameroon", "philippines", "toshka", "tem", "biophysics", "microfluidics", "public health"}
KEYWORD_THEMES.update({
    # assessment methods
    "sustainability assessment": [SUST], "sustainability metrics": [SUST], "key performance indicators": [SUST],
    "social impact assessment": [SUST, POL], "eco-innovation measurement": [SUST, POL], "environmental systems analysis": [SUST],
    "carbon and biodiversity loss assessment": [SUST, BIO], "carbon accounting": [CLIM, SUST], "biogenic carbon accounting": [CLIM, LCA],
    "hybrid lca": [LCA, IO], "lca-mfa": [LCA, MFA], "ecodesign & ssbd": [LCA, CIRC], "pharmaceutical manufacturing": [LCA],
    "prospective technologies": [LCA], "environmental risk assessment": [TOX], "nanosafety": [TOX], "probability theory": [DEC],
    # resources, energy, built environment
    "climate change": [CLIM, EARTH], "climate": [CLIM], "climate mitigation": [CLIM], "energy transition": [CLIM], "energy": [CLIM],
    "energy economics": [CLIM, POL], "energy analysis": [CLIM], "energy markets & systems": [CLIM, POL],
    "energy science and technology": [CLIM], "hydrogen economy": [CLIM], "thermochemical hydrogen production": [CLIM],
    "greenhouse gas emissions": [CLIM], "industrial decarbonisation": [CLIM], "low-carbon technology": [CLIM],
    "carbon capture and storage": [CLIM], "direct air capture": [CLIM], "carbon pricing": [CLIM, POL], "climate economics": [CLIM, POL],
    "sustainable and net-zero maritime transitions": [CLIM, POL], "coastal climate adaptation": [WAT, CLIM],
    "steel": [CIRC, CLIM], "steel by-products": [CIRC], "materials science": [CIRC],
    "urban mining": [CIRC, BUILT], "recycling": [CIRC], "waste management": [CIRC], "waste and material recovery": [CIRC],
    "informal waste sector": [CIRC, POL], "solid waste management systems": [CIRC], "circular design": [CIRC],
    "product design engineering": [CIRC], "digital product passport": [CIRC, DATA], "electrical & electronic devices": [CIRC],
    "rare earth magnets": [CIRC], "mineral supply chains": [CIRC], "sustainable mining": [CIRC], "tailings management": [CIRC, TOX],
    "supply chain resilience": [CIRC], "supply chain vulnerability assessment": [CIRC], "sand": [CIRC, BUILT],
    "construction and demolition materials": [CIRC, BUILT], "circular construction": [CIRC, BUILT],
    "circular built environment": [CIRC, BUILT], "circular cities": [CIRC, BUILT], "industrial symbiosis": [CIRC],
    "valorisation": [CIRC], "bioeconomy": [CIRC, FOOD], "forest-based products": [CIRC, LAND], "resources use": [CIRC],
    "sustainable manufacturing": [CIRC], "urban planning": [BUILT], "urban resilience": [BUILT], "sustainable cities": [BUILT],
    "green cities": [URB, BUILT], "cities": [BUILT], "built environment sustainability": [BUILT], "infrastructure development": [BUILT],
    "building stock modelling": [BUILT, MFA], "circular urban system modeling": [CIRC, BUILT], "building performance": [BUILT, CLIM],
    "positive-energy urban design": [BUILT, CLIM], "landscape design": [LAND, URB], "urban biodiversity": [URB, BIO],
    # society, food, policy
    "environmental economics": [POL], "economics": [POL], "macroeconomics": [POL], "ecological economics": [POL, WELL],
    "economic complexity": [POL], "economics for transition": [POL, WELL], "well-being economy": [WELL, POL],
    "economic affordability": [POL], "policy analysis": [POL], "environmental policy": [POL], "environmental policy instrumentation": [POL],
    "sustainability transitions": [POL], "transition management": [POL], "sociotechnical transitions": [POL], "sociotechnical systems": [POL],
    "environmental justice": [POL, WELL], "environmental crime and legislation": [POL], "international development": [POL],
    "political philosophy": [POL], "science-policy interface": [POL], "knowledge-to-action transfer": [POL], "transformative change": [POL],
    "social-ecological systems": [ES, POL], "poverty and health outcomes": [WELL], "community livelihoods": [WELL],
    "inclusion and sustainability": [WELL], "sustainable lifestyles": [WELL], "affective forecasting": [WELL],
    "sustainable agriculture": [FOOD, LAND], "agriculture": [FOOD], "agricultural systems": [FOOD], "agroecosystems": [FOOD, BIO],
    "sustainable farming": [FOOD], "food systems": [FOOD], "global food system": [FOOD], "food waste": [FOOD, CIRC],
    "dietary pattern": [FOOD], "sustainable diet": [FOOD], "dietary change": [FOOD], "food sustainability": [FOOD],
    "land and food system": [FOOD, LAND], "plant-based protein": [FOOD], "lupin beans": [FOOD], "antibiotics in livestock": [FOOD, TOX],
    "sustainable agriculture, soil carbon cycle": [FOOD, SOIL],
    # biodiversity and ecosystems
    "microbial ecology": [SOIL], "microbes": [SOIL], "microbial communities": [SOIL], "soil microbiome": [SOIL],
    "host-microbe interactions": [SOIL], "fungal taxonomy": [SOIL], "soil health": [SOIL], "soil science": [SOIL],
    "soil carbon cycle": [SOIL, NUTR], "nanosims": [SOIL], "metagenomics": [GEN, SOIL], "molecular biology": [GEN],
    "molecular ecology": [GEN], "evolutionary biology": [GEN], "evolutionary ecology": [GEN, BIO], "evolution": [GEN],
    "landscape genetics": [GEN, LAND], "environmental rna (erna)": [GEN], "natural products": [GEN], "bioinformatics": [DATA, GEN],
    "computational biology": [DATA, GEN], "taxonomy": [BIO], "field ecology": [BIO], "field biology": [BIO],
    "population ecology": [BIO], "functional diversity": [BIO], "trophic network": [BIO], "community composition": [BIO],
    "terrestrial ecosystems": [BIO], "ecosystems": [BIO], "mediterranean ecology": [BIO], "insect ecology": [BIO],
    "environmental heterogeneity": [BIO], "scale dependence": [BIO], "index of species roles": [BIO],
    "ecotoxicology, biodiversity": [TOX, BIO], "plant-pollinator networks": [BIO, PLANT], "plant-insect dynamics": [PLANT, BIO],
    "mosquito-plant interactions": [BIO], "vector-borne disease risk": [BIO], "sponges": [WAT, BIO], "tropical forests": [PLANT, LAND],
    "plant ecology": [PLANT], "plant science": [PLANT], "plant physiology": [PLANT], "intraspecific trait variation": [PLANT],
    "trait database": [PLANT, DATA], "aquatic- plant ecology": [WAT, PLANT], "behavioural ecology": [ANIM],
    "mosquito ecology": [ANIM], "herpetology": [ANIM], "crocodiles": [ANIM, CONS], "avian ecology": [ANIM],
    "avian functional group": [ANIM], "sex ratios": [ANIM], "physiology & behavior": [ANIM], "prey-predator interactions": [ANIM],
    "human-carnivore co-existence": [ANIM, CONS], "wildlife management": [ANIM, CONS], "rangeland management": [LAND, ANIM],
    "biodiversity conservation": [CONS], "biodiversity monitoring": [CONS, GEN], "invasion biology": [CONS],
    "amphibian pathogens": [BIO, CONS], "citizen science": [CONS], "monitoring": [CONS], "human-biodiversity interactions": [ES],
    "global change biology": [EARTH, BIO], "climate-nature interactions": [EARTH, BIO], "paleoecology": [EARTH],
    # pollution, water, earth system
    "toxicology": [TOX], "nanotoxicology": [TOX], "computational toxicology": [TOX, DATA], "nitrogen pollution": [NUTR, TOX],
    "light pollution": [TOX], "noise pollution": [TOX], "anthropogenic stressors": [TOX], "ecosystem health": [TOX, BIO],
    "analytical measurement": [TOX], "laboratory analysis": [TOX], "micropollutant analysis": [TOX, WAT], "litter": [TOX],
    "phytoremediation": [TOX, PLANT], "daphnia ssp.": [TOX, WAT], "rivers": [WAT], "hydrology": [WAT],
    "marine biodiversity": [WAT, BIO], "ground water quality": [WAT], "environmental tracer": [WAT], "soil salinity": [SOIL, WAT],
    "nutrient cycling": [NUTR, SOIL], "biogeochemical cycles": [NUTR, EARTH], "nitrogen studies": [NUTR], "carbon dynamics": [NUTR, EARTH],
    "aerosol retrievals": [EARTH, SPAT], "atmospheric modelling": [EARTH, MOD], "shared socioeconomic pathways": [MOD, EARTH],
    # modelling and data
    "machine learning": [DATA], "data science": [DATA], "deep learning": [DATA], "computer vision": [DATA],
    "environmental data science": [DATA], "information systems": [DATA], "data analysis": [DATA], "data management": [DATA],
    "database management": [DATA], "fair data": [DATA], "algorithms": [DATA], "computer science": [DATA],
    "statistical methods": [DATA], "mixed models": [DATA], "multivariate analysis": [DATA], "computational ecology": [DATA, BIO],
    "mathematical methods": [MOD], "mathematical modelling": [MOD], "modelling": [MOD], "integrated modeling": [MOD],
    "ecological modelling": [MOD, BIO], "process modelling": [MOD], "data assimilation": [MOD], "inverse problem": [MOD],
    "prospective scenarios": [MOD], "complex systems engineering": [MOD], "networked systems": [MOD], "system design": [MOD],
    "process system engineering (pse)": [MOD], "nutrient model": [NUTR, MOD], "land use modelling": [LAND, MOD],
    "species distribution modelling": [SPAT, BIO], "remote sensing": [SPAT], "hyperspectral analysis": [SPAT],
    "radiative transfer models": [SPAT], "unmanned aerial vehicles": [SPAT], "land use": [LAND],
})

# ---------- layer 3: text back-fill (title + bio), word-boundary regexes, threshold TEXT_MIN matches ----------
TEXT_PATTERNS = {
    LCA: r"life[- ]cycle|\blca\b|\blcia\b",
    MFA: r"material flow|substance flow|material stock|metabolism",
    IO: r"input[- ]output|footprint|supply[- ]use",
    SUST: r"sustainability assessment|impact assessment|indicators?",
    DEC: r"decision[- ]making|decision support|multi-criteria|\bmcda\b|uncertaint",
    CIRC: r"circular|recycl|critical (raw )?materials|resource (use|supply)",
    CLIM: r"climate|decarboni[sz]|\benergy\b|emissions",
    BUILT: r"\bbuildings?\b|built environment|construction|mobility",
    FOOD: r"\bfoods?\b|diets?\b|agricultur",
    WELL: r"well-?being|post-growth|sufficiency",
    POL: r"\bpolic(y|ies)\b|governance",
    BIO: r"biodiversity|community ecology|species|food webs?",
    CONS: r"conservation|invasi|restoration|rewilding",
    ANIM: r"\bbirds?\b|mammals?|wildlife|herbivor|movement ecology",
    PLANT: r"\bplants?\b|vegetation|\btrees?\b|grassland",
    SOIL: r"\bsoils?\b|fungi|fungal|microb",
    ES: r"ecosystem services?|nature-based|natural capital",
    URB: r"urban (nature|ecology|green)|green infrastructure",
    GEN: r"\bdna\b|genetic|genom|molecular",
    TOX: r"ecotoxic|pesticide|contaminant|plastic|chemical (stress|pollut|risk)|pollution",
    WAT: r"freshwater|aquatic|\bocean|\bmarine\b|\bwater\b|\bcoast",
    LAND: r"land[- ]use|landscape",
    EARTH: r"earth system|global change|planetary",
    NUTR: r"nitrogen|nutrient|biogeochem|phosphorus",
    MOD: r"scenario|integrated assessment|modell?ing|simulation",
    DATA: r"machine learning|artificial intelligence|\bdata science|software|big data",
    SPAT: r"\bgis\b|remote sensing|geospatial|spatial",
}
MIN_KEYWORD_THEMES = 3  # people with fewer keyword-derived themes also get text-derived ones
MAX_THEMES = 6
# Only the start of the bio is used (later parts are mostly career history). People with no keywords at all
# rely on text alone, so a single match in a short bio counts; otherwise text must repeat to count.
TEXT_RULES = {"with_keywords": {"chars": 1500, "min_hits": 3}, "no_keywords": {"chars": 700, "min_hits": 1}}


# ---------- CML research themes (the institute's five themes) ----------
# Each fine theme contributes a weight to each CML theme; bio/title text adds a little more. Everyone gets at least
# one theme: people below CML_MIN fall back to their best-scoring theme, then to their department's home theme.
CML_THEMES = {   # name -> colour (icons live in the dashboard)
    "Biodiversity": "#2f9e44", "Future food": "#e8a317", "Resources": "#7048e8", "Urban": "#e64980", "Water": "#1c7ed6",
}
DEPT_DEFAULT = {"industrial_ecology": "Resources", "environmental_biology": "Biodiversity"}
CML_WEIGHTS = {
    "Biodiversity": {BIO: 1, CONS: 1, ANIM: 1, PLANT: 1, SOIL: 1, GEN: 1, ES: .7, TOX: .8, URB: .4},
    "Future food": {FOOD: 1.5, NUTR: .5},
    "Resources": {CIRC: 1.2, MFA: 1, IO: .7, LCA: 1.0, BUILT: .4, CLIM: .3},
    "Urban": {BUILT: 1.2, URB: 1.2, ES: .2, SPAT: .2},
    "Water": {WAT: 1.2, NUTR: .3, TOX: .4, EARTH: .2},
}
# Direct keyword -> CML theme weights, added on top of the fine-theme weights above. Needed because the fine themes
# are too coarse for Urban: e.g. "urban studies" has no fine theme of its own and was silently lost.
CML_KEYWORDS = {
    "urban studies": {"Urban": .6},   # alone stays below CML_MIN; needs corroboration from bio text "urban design": {"Urban": 1.5}, "urban ecology": {"Urban": 1.5, "Biodiversity": .5},
    "urban natural capital": {"Urban": 1.5}, "green infrastructures": {"Urban": 1.2}, "built environment": {"Urban": 1.5},
    "buildings": {"Urban": 1.5}, "transport": {"Urban": 1.2}, "construction materials": {"Urban": 1.2},
    "concrete recycling": {"Urban": 1.0}, "construction and demolition waste management": {"Urban": 1.2},
    "socioeconomic metabolism": {"Urban": .8}, "consumption-based emissions": {"Urban": .5},
    "human-nature interactions": {"Urban": .5}, "nature-based solutions": {"Urban": .4},
    "coastal management": {"Urban": .3}, "sandy coasts": {"Urban": .3},
    "aquatic invertebrates": {"Water": .8}, "freshwater biodiversity": {"Water": .6}, "plankton": {"Water": .6},
    "microplastics": {"Water": .3}, "plastic pollution": {"Water": .6}, "water-energy-land system": {"Water": .5},
    "sustainable food systems": {"Future food": 1}, "global food trade": {"Future food": 1}, "animal welfare": {"Future food": .8},
    "artificial fertilizer": {"Future food": 1}, "pesticides": {"Future food": .6},
}
CML_KEYWORDS.update({   # added for the full staff list
    "urban planning": {"Urban": 1.2}, "sustainable cities": {"Urban": 1.2}, "green cities": {"Urban": 1.2},
    "cities": {"Urban": 1.2}, "urban resilience": {"Urban": 1.2}, "circular cities": {"Urban": 1.2},
    "circular built environment": {"Urban": 1.2}, "circular construction": {"Urban": 1.0}, "urban mining": {"Urban": .8},
    "building stock modelling": {"Urban": 1.2}, "urban biodiversity": {"Urban": 1.5, "Biodiversity": .5},
    "positive-energy urban design": {"Urban": 1.5}, "building performance": {"Urban": 1.2}, "landscape design": {"Urban": .6},
    "rivers": {"Water": 1}, "hydrology": {"Water": 1}, "marine biodiversity": {"Water": 1}, "ground water quality": {"Water": 1},
    "coastal climate adaptation": {"Water": 1}, "sponges": {"Water": .6}, "daphnia ssp.": {"Water": .8},
    "micropollutant analysis": {"Water": .6}, "sustainable and net-zero maritime transitions": {"Water": .6},
    "sustainable agriculture": {"Future food": 1}, "agriculture": {"Future food": 1}, "agroecosystems": {"Future food": 1},
    "sustainable farming": {"Future food": 1}, "food systems": {"Future food": 1}, "global food system": {"Future food": 1},
    "food waste": {"Future food": 1}, "dietary change": {"Future food": 1}, "sustainable diet": {"Future food": 1},
    "food sustainability": {"Future food": 1}, "plant-based protein": {"Future food": 1}, "antibiotics in livestock": {"Future food": 1},
})
CML_TEXT = {   # per-hit weight is CML_TEXT_HIT, capped at CML_TEXT_CAP hits, so one theme can add at most ~1.6
    "Biodiversity": r"biodiversity|(?<!industrial )ecolog|\bspecies|conservation|\bwildlife",
    "Future food": r"\bfoods?\b|agricultur|\bfarm|\bdiets?\b|horticultur|\bcrops?\b|fertili[sz]",
    "Resources": r"circular|critical (raw )?materials|recycl|resource|material (flow|stock)|\bmetals?\b|supply chain",
    "Urban": (r"\burban|\bcit(y|ies)\b|\bbuildings?\b|built environment|housing|neighbou?rhood|spatial planning|"
              r"municipal|mobility|construction (sector|material|and demolition|industry)|demolition|(urban|green|transport) infrastructure"),
    "Water": r"\bwater|aquatic|freshwater|\bmarine\b|\bocean|\bcoast|hydrolog|\blakes?\b|\brivers?\b|plankton",
}
CML_TEXT_CHARS = 2500   # bios are front-loaded; later parts are mostly career history
CML_TEXT_HIT, CML_TEXT_CAP = 0.4, 4
# Publications (OpenAlex titles + keywords, see build_network.py) are a further, lower-weight layer: each CML theme
# adds CML_PUB_WEIGHT x the share of the person's papers that match its CML_TEXT pattern. It mostly matters for
# PhD candidates and others whose profile has few keywords and no bio.
CML_PUB_WEIGHT = 1.5
PUB_MIN_PAPERS = 2        # a fine theme from publications needs at least this many matching papers ...
PUB_MIN_SHARE = 0.2       # ... and this share of the person's papers
PUB_TAG_WEIGHT = 0.75     # per matching paper (capped at 4); profile keywords count 3 each
CML_MIN = 0.7           # absolute floor for a theme to be listed
CML_REL = 0.4           # ...and it must reach this share of the person's top score (or CML_STRONG outright)
CML_STRONG = 1.5
CML_MAX = 3
CML_OVERRIDES = {   # person name -> ordered CML themes (first = primary), for manual corrections
    # scores put the wrong theme first, or miss a clear secondary theme
    "Valerio Barbarossa": ["Water", "Biodiversity", "Resources"],  # freshwater biodiversity, hydrology, hydropower/mining
    "Ester van der Voet": ["Resources", "Urban"],  # MFA / substance flows; urban metabolism
    "José Mogollón": ["Water", "Future food", "Biodiversity", "Resources", "Urban"],  # earth-system / nutrient cycles touch every theme
    "Mingming Hu": ["Urban", "Resources"],  # applies industrial ecology to the built environment (housing, construction, demolition waste)
    "Bernhard Steubing": ["Resources", "Urban"],  # LCA methods; applications in electric mobility and the built environment
    "Carlos Felipe Blanco Rocha": ["Resources", "Biodiversity"],  # LCA / safe-and-sustainable-by-design; risk assessment is secondary
    "Arjan de Koning": ["Resources"],  # LCIA characterisation factors, environmental chemistry
    "Peter Berrill": ["Urban", "Resources"],  # buildings, transport and energy at urban scale
    "Tomer Fishman": ["Resources", "Urban"],  # material stocks of buildings and infrastructure
    "Rita de Sousa e Silva": ["Urban", "Biodiversity"],  # assistant professor in Urban Ecology
    "Stefano Merciai": ["Resources", "Future food"],  # input-output / supply-use tables; "agriculture" hits are career history
    "Patrik Henriksson": ["Future food", "Water"],  # humans and the ocean, with emphasis on food; LCA is only a method
    "Martina Vijver": ["Biodiversity", "Water"],  # ecotoxicology of nanomaterials, pesticides, metals, microplastics; mesocosms and field ecosystems
    "Peter van Bodegom": ["Biodiversity", "Future food"],  # also professor of Circular International Horticulture Systems
    "Geert de Snoo": ["Biodiversity", "Future food"],  # biodiversity and agriculture, pesticides, sustainable land use
    # methods or interdisciplinary profiles with no scored theme: assigned to the theme their applications sit in
    "Marco Cinelli": ["Resources"],  # decision support; applications in energy systems, materials and chemical safety
    "Hai Lin": ["Resources"],  # HPC/modelling; energy-grid stability is the environmental application on his profile
    "Lena Kilian": ["Resources", "Urban"],  # consumption-based emissions, subnational / household footprints
    "Jan Boersema": ["Biodiversity"],  # sustainability, nature and worldviews; biologist by training, closest theme is nature
    "Koos Biesmeijer": ["Biodiversity"],  # chair in Natural Capital; pollinators and insect decline (no bio scraped)
    # no bio or keywords scraped and nothing found online: PROVISIONAL, department home theme. Confirm and replace.
    "Sónia Martins da Cunha": ["Resources", "Urban"],
    "Lotte de Vries": ["Biodiversity"],
}


def assign_cml(p):
    score = Counter()
    for theme in p["harmonized_tags"]:
        for cml, w in CML_WEIGHTS.items():
            score[cml] += w.get(theme, 0)
    for k in p["original_keywords"]:
        for cml, w in CML_KEYWORDS.get(k, {}).items():
            score[cml] += w
    text = f"{p.get('title', '')} {(p.get('summary') or '')[:CML_TEXT_CHARS]}".lower()
    for cml, pat in CML_TEXT.items():
        score[cml] += CML_TEXT_HIT * min(len(re.findall(pat, text)), CML_TEXT_CAP)
    pubs = [t.lower() for t in p.get("pub_texts") or []]
    if pubs:
        for cml, pat in CML_TEXT.items():
            score[cml] += CML_PUB_WEIGHT * sum(1 for t in pubs if re.search(pat, t)) / len(pubs)
    if p["name"] in CML_OVERRIDES:
        themes = CML_OVERRIDES[p["name"]]
    else:
        ranked = [(c, v) for c, v in sorted(score.items(), key=lambda kv: -kv[1]) if v >= CML_MIN]
        themes = [c for c, v in ranked if v >= CML_REL * ranked[0][1] or v >= CML_STRONG][:CML_MAX] if ranked else []
        if not themes:   # weak evidence: best-scoring theme if any, else the department's home theme
            best = max(score, key=score.get) if score else None
            themes = [best] if best else [DEPT_DEFAULT[p["field"]]]
    p["cml_themes"] = themes
    p["cml_scores"] = {c: round(v, 2) for c, v in score.items() if v > 0}


def canonical(keyword):
    k = re.sub(r"\s+", " ", keyword.lower().strip())
    k = ALIASES.get(k, k)
    return None if k in DROP else k


def harmonize_person(p):
    raw = list(p.get("original_keywords") or []) + KEYWORD_EXTRA.get(p["name"], [])
    kws, seen = [], set()
    for k in raw:
        c = canonical(k)
        if c and c not in seen:
            seen.add(c); kws.append(c)
    score = Counter()
    for k in kws:
        for t in KEYWORD_THEMES.get(k, []):
            score[t] += 3
    if len(score) < MIN_KEYWORD_THEMES:
        rule = TEXT_RULES["with_keywords" if kws else "no_keywords"]
        text = f"{p.get('title', '')} {(p.get('summary') or '')[:rule['chars']]}".lower()
        for theme, pat in TEXT_PATTERNS.items():
            n = len(re.findall(pat, text))
            if n >= rule["min_hits"]:
                score[theme] += min(n, 4)
    pubs = [t.lower() for t in p.get("pub_texts") or []]
    if len(score) < MIN_KEYWORD_THEMES and pubs:
        for theme, pat in TEXT_PATTERNS.items():
            n = sum(1 for t in pubs if re.search(pat, t))
            if n >= PUB_MIN_PAPERS and n >= PUB_MIN_SHARE * len(pubs):
                score[theme] += PUB_TAG_WEIGHT * min(n, 4)
    themes = [t for t, _ in sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))][:MAX_THEMES]
    return kws, themes


def apply(data):
    """Rewrite original_keywords (canonical) and harmonized_tags in place; return the taxonomy for the page."""
    unmapped = Counter()
    for plist in data.values():
        for p in plist:
            p["raw_keywords"] = list(p.get("original_keywords") or [])
            p["original_keywords"], p["harmonized_tags"] = harmonize_person(p)
            assign_cml(p)
            unmapped.update(k for k in p["original_keywords"] if k not in KEYWORD_THEMES)
    return {
        "areas": AREAS,
        "cml": CML_THEMES,
        "cmlWeights": CML_WEIGHTS,
        "themeArea": THEME_AREA,
        "keywordThemes": {k: v for k, v in KEYWORD_THEMES.items()},
        "unmapped": dict(unmapped),
    }


if __name__ == "__main__":
    src = Path(__file__).parent / "leiden_professors_classified.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    tax = apply(data)
    print("unmapped keywords:", tax["unmapped"] or "none")
    for plist in data.values():
        for p in plist:
            print(f"{p['name']} [{p['field'][:2]}]: {' + '.join(p['cml_themes'])}   {p['cml_scores']}")
    print(Counter(t for v in data.values() for p in v for t in p["cml_themes"]).most_common())
