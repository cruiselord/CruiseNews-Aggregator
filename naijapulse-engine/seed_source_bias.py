#!/usr/bin/env python3
"""
Phase 5 — populate public.source_bias in the LIVE Supabase project with the full
ownership/bias profiles (Perplexity research + user-provided TheCable profile).

Idempotent: resolves source_id by name, then upserts on source_id (PK). Safe to
re-run. Does NOT touch the local SQLite mirror.
"""
import os
import sys
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

URL = os.getenv("SUPABASE_URL")
KEY = os.getenv("SUPABASE_KEY")
if not URL or not KEY:
    sys.exit("SUPABASE_URL / SUPABASE_KEY not found in env (.env)")

supabase: Client = create_client(URL, KEY)

PROFILES = [
    {
        "name": "TheCable",
        "ownership_lean": "anti_government",
        "regional_base": "south_west",
        "confidence": "high",
        "notes": ("Lagos-based digital outlet launched in 2014 with a stated mission of "
                  "knowledge-driven journalism in pursuit of Nigeria's progress; widely "
                  "regarded for professional reporting, investigations, and watchdog "
                  "coverage of corruption and governance, while branding itself explicitly "
                  "as Nigeria's independent online newspaper."),
        "owner": ("Cable Media & Publishing Ltd (also known as Cable Newspaper Ltd), "
                  "founded and controlled by Simon Kolawole"),
        "ownership_type": "private_corporate",
        "political_alignment": ("Positions itself as an independent, accountability-focused "
                                "online newspaper that frequently investigates and critiques "
                                "successive federal administrations, including APC-led "
                                "governments, rather than aligning with a specific party bloc."),
        "source_urls": ["https://www.thecable.ng/about-us/",
                        "https://en.wikipedia.org/wiki/TheCable"],
    },
    {
        "name": "BusinessDay",
        "ownership_lean": "mixed",
        "regional_base": "south_west",
        "confidence": "medium",
        "notes": ("Focuses on business, finance, and economic policy, with a right-center, "
                  "pro-growth editorial tone and mixed factual rating noted by external "
                  "reviewers; coverage can be critical of government economic missteps but "
                  "often supports market-oriented reforms and private sector perspectives."),
        "owner": ("BusinessDay Media Limited (majority management-owned; publisher Frank "
                  "Aigbogun as controlling equity holder and CEO)"),
        "ownership_type": "private_corporate",
        "political_alignment": ("Generally pro-market and business-friendly, with coverage "
                                "often sympathetic to reform-oriented federal administrations "
                                "but not consistently aligned with a single party."),
        "source_urls": ["https://about.businessday.ng/index.php",
                        "https://mediabiasfactcheck.com/business-day-nigeria-bias/"],
    },
    {
        "name": "Daily Post",
        "ownership_lean": "independent",
        "regional_base": "south_west",
        "confidence": "medium",
        "notes": ("Online-only outlet based in Lagos with broad coverage of Nigerian "
                  "politics, metro and entertainment; external reviewers describe it as "
                  "least-biased, though sourcing can be basic and coverage sometimes relies "
                  "heavily on official statements and press releases."),
        "owner": "Daily Post Media Limited (owned by publisher James Bamisaye)",
        "ownership_type": "private_corporate",
        "political_alignment": ("Publicly positions itself as a general-interest, non-partisan "
                                "outlet covering APC, PDP and other actors with relatively "
                                "balanced tone, without a clear institutional alignment."),
        "source_urls": ["https://dailypost.ng/about/",
                        "https://mediabiasfactcheck.com/daily-post-nigeria-bias/"],
    },
    {
        "name": "Daily Trust",
        "ownership_lean": "mixed",
        "regional_base": "north",
        "confidence": "medium",
        "notes": ("Strong northern Nigeria focus and reputation for representing northern "
                  "socio-political perspectives; rated left-center by an external bias "
                  "monitor, with recurring government complaints that its editorials "
                  "exaggerate hardship and distort policy, but also respected for broad "
                  "national coverage and Hausa-language editions."),
        "owner": "Media Trust Limited (founded and chaired by Kabiru Abdullahi Yusuf)",
        "ownership_type": "private_corporate",
        "political_alignment": ("Northern-rooted outlet that frequently scrutinises federal "
                                "administrations, including the current APC-led government, "
                                "while also maintaining relationships with key federal "
                                "institutions, leading to a perception of alternating critical "
                                "and cooperative coverage."),
        "source_urls": ["https://mediabiasfactcheck.com/daily-trust-bias/",
                        "https://en.wikipedia.org/wiki/Media_Trust"],
    },
    {
        "name": "Guardian NG",
        "ownership_lean": "mixed",
        "regional_base": "south_west",
        "confidence": "medium",
        "notes": ("Positions itself explicitly as an independent newspaper owing no "
                  "allegiance to any political party or interest group; Lagos-based with "
                  "long-standing reputation for quality national reportage, but opinion pages "
                  "often reflect centrist-to-liberal urban elite perspectives."),
        "owner": ("Guardian Press Limited (majority shareholder) and members of the Ibru "
                  "family, including Lady Maiden Ibru, Toke Ibru, and Tive Ibru"),
        "ownership_type": "private_corporate",
        "political_alignment": ("Historically seen as an elite, relatively liberal national "
                                "outlet that alternates between critical watchdog coverage and "
                                "establishment-friendly editorials, without a stable alignment "
                                "to APC or PDP."),
        "source_urls": ["https://guardian.ng/ownership-funding/",
                        "https://en.wikipedia.org/wiki/The_Guardian_(Nigeria)"],
    },
    {
        "name": "Premium Times",
        "ownership_lean": "anti_government",
        "regional_base": "national",
        "confidence": "high",
        "notes": ("Abuja-based digital-native outlet known for investigative reporting and "
                  "anti-corruption coverage, supported by grants via its non-profit arm "
                  "(CJID/PTCIJ); external reviewers rate it left-center with generally "
                  "factual reporting, and it is often cited as a leading "
                  "accountability-focused newsroom."),
        "owner": "Premium Times Services Limited (publisher and co-founder Dapo Olorunyomi)",
        "ownership_type": "private_corporate",
        "political_alignment": ("Widely perceived as an investigative, non-partisan watchdog "
                                "that frequently exposes corruption and abuses across "
                                "successive federal administrations, including APC-led "
                                "governments, rather than aligning with a particular party."),
        "source_urls": ["https://mediabiasfactcheck.com/premium-times-bias-and-credibility/",
                        "https://en.wikipedia.org/wiki/Premium_Times"],
    },
    {
        "name": "Punch",
        "ownership_lean": "mixed",
        "regional_base": "south_west",
        "confidence": "high",
        "notes": ("Oldest mass-market private daily still in circulation, with strong "
                  "national reach; rated left-center with mixed factual reporting by an "
                  "external bias monitor, reflecting balanced straight news but opinion "
                  "pieces that lean towards social-justice and pro-poor critiques of "
                  "government."),
        "owner": ("Punch Nigeria Limited (family-controlled, chaired by Angela Olufunmilayo "
                  "Emuwa, nee Aboderin)"),
        "ownership_type": "private_corporate",
        "political_alignment": ("Urban, Lagos-based daily with balanced news coverage of APC "
                                "and PDP but a left-of-center editorial page that often "
                                "criticises economic hardship and governance under recent "
                                "APC-led administrations."),
        "source_urls": ["https://mediabiasfactcheck.com/the-punch-nigeria/",
                        "https://en.wikipedia.org/wiki/The_Punch"],
    },
    {
        "name": "The Nation",
        "ownership_lean": "pro_government",
        "regional_base": "south_west",
        "confidence": "high",
        "notes": ("Founded in 2006 from the former Comet newspaper and published by Vintage "
                  "Press Limited; Tinubu has publicly confirmed being a promoter and "
                  "financial investor, and the outlet is often criticised as a partisan, "
                  "pro-APC platform despite providing routine national news coverage."),
        "owner": ("Vintage Press Limited (promoted and financially backed by Bola Ahmed "
                  "Tinubu and allied investors)"),
        "ownership_type": "private_corporate",
        "political_alignment": ("Strongly associated with the APC and widely perceived as "
                                "aligned with Bola Tinubu's political bloc, often defending "
                                "or promoting APC-led federal and Lagos State administrations."),
        "source_urls": ["https://thenationonlineng.net/about-us/",
                        "https://neusroom.com/tinubu-confirms-business-interests-in-tvc-and-the-nation/"],
    },
    {
        "name": "ThisDay",
        "ownership_lean": "mixed",
        "regional_base": "national",
        "confidence": "medium",
        "notes": ("Founded in 1995 by Nduka Obaigbena, who has historically maintained "
                  "high-level ties to federal officials; the group has previously received "
                  "controversial federal payments routed through General Hydrocarbons, "
                  "fuelling perceptions of establishment alignment, but it also carries "
                  "diverse opinions and broad national reportage."),
        "owner": "Leaders & Company Limited (controlled by founder and publisher Nduka Obaigbena)",
        "ownership_type": "private_corporate",
        "political_alignment": ("Elite, Abuja- and Lagos-connected outlet often seen as close "
                                "to ruling circles and business elites, with coverage and "
                                "events that can be sympathetic to incumbent federal "
                                "administrations while occasionally publishing critical "
                                "commentary."),
        "source_urls": ["https://en.wikipedia.org/wiki/This_Day",
                        "https://thewhistler.ng/dasukigate-thisday-newspaper-boss-nduka-obaigbena-writes-to-efcc/"],
    },
    {
        "name": "Tribune",
        "ownership_lean": "mixed",
        "regional_base": "south_west",
        "confidence": "medium",
        "notes": ("Oldest privately owned Nigerian newspaper still publishing, with roots in "
                  "Ibadan and strong Yoruba readership; long known for opposition-friendly and "
                  "anti-authoritarian stances against military and authoritarian governments, "
                  "while today offering a mix of regional and national coverage."),
        "owner": ("African Newspapers of Nigeria PLC (founded by Chief Obafemi Awolowo; "
                  "historically overseen by the Awolowo family)"),
        "ownership_type": "private_corporate",
        "political_alignment": ("Historically associated with Awolowo's progressive, "
                                "Yoruba-south-west political base and, in later years, often "
                                "seen as sympathetic to centre-left and opposition "
                                "perspectives, though current coverage includes a broader "
                                "national and multi-party focus."),
        "source_urls": ["https://en.wikipedia.org/wiki/Nigerian_Tribune",
                        "https://tribuneonlineng.com/about-us/"],
    },
    {
        "name": "Vanguard",
        "ownership_lean": "mixed",
        "regional_base": "south_west",
        "confidence": "medium",
        "notes": ("Privately owned Lagos-based newspaper launched in the 1980s, historically "
                  "part of a cohort of private dailies that reported on corruption despite "
                  "government pressure; today it is known for broad coverage, sensational "
                  "headlines, and publishing diverse political opinions, sometimes drawing "
                  "criticism for variable depth and sensationalism."),
        "owner": "Vanguard Media Limited (founded and published by Sam Amuka-Pemu)",
        "ownership_type": "private_corporate",
        "political_alignment": ("Mass-market national daily often perceived as pragmatically "
                                "aligned with whichever federal administration is in power, "
                                "giving space to both government narratives and opposition "
                                "criticism without a consistent partisan line."),
        "source_urls": ["https://en.wikipedia.org/wiki/Vanguard_(Nigeria)",
                        "https://en.wikipedia.org/wiki/Nigerian_Tribune"],
    },
    # === NEW SOURCES (added 2026-07-14) ===
    {
        "name": "Channels TV",
        "ownership_lean": "mixed",
        "regional_base": "south_west",
        "confidence": "medium",
        "notes": ("Lagos-based 24-hour news station known for live coverage and talk shows; it emphasizes balanced, objective reporting in its mission statement and has won multiple Nigerian Media Merit Awards, but critics occasionally describe it as soft on those in power due to its access-driven model."),
        "owner": "Channels Television (Channels Media Group), founded and controlled by chief executive John Momoh and his wife Sola Momoh",
        "ownership_type": "private_corporate",
        "political_alignment": ("Widely regarded as a relatively professional, establishment-friendly national TV news channel that platforms government officials and opposition figures, with a tendency toward cautious coverage of incumbent federal administrations (currently APC-led) rather than overt partisanship."),
        "source_urls": ["https://en.wikipedia.org/wiki/Channels_TV", "https://www.squidtv.net/africa/nigeria/nigeria-001.html"],
    },
    {
        "name": "Sahara Reporters",
        "ownership_lean": "anti_government",
        "regional_base": "national",
        "confidence": "high",
        "notes": ("Online investigative and citizen-journalism platform founded in 2006 that focuses on exposing corruption, human rights abuses, and election malpractice; it has received grants from international foundations such as the Ford Foundation and Omidyar Network, fueling criticism about foreign influence."),
        "owner": "Sahara Reporters Media Group (U.S.-registered, founded and led by Omoyele Sowore)",
        "ownership_type": "private_corporate",
        "political_alignment": ("Strongly anti-corruption and adversarial toward successive Nigerian governments, especially PDP and APC administrations, and widely perceived as a radical opposition-leaning outlet."),
        "source_urls": ["https://saharareporters.com/about", "https://www.biasly.com/sources/sahara-reporters-media-bias/"],
    },
    {
        "name": "Peoples Gazette",
        "ownership_lean": "anti_government",
        "regional_base": "national",
        "confidence": "medium",
        "notes": ("Online newspaper launched in 2020 that emphasizes investigative and accountability reporting; it has faced pressure and harassment from authorities over its critical coverage."),
        "owner": "Peoples Gazette Ltd (Gazette Nigeria), co-founded and edited by publisher Samuel Ogundipe",
        "ownership_type": "private_corporate",
        "political_alignment": ("Strongly critical of Nigeria's political establishment, with frequent investigative and opinion pieces targeting APC-led federal administrations and security agencies."),
        "source_urls": ["https://en.wikipedia.org/wiki/Peoples_Gazette", "https://gazettengr.com/about-us/"],
    },
    {
        "name": "Leadership",
        "ownership_lean": "mixed",
        "regional_base": "north",
        "confidence": "medium",
        "notes": ("Abuja-based national daily long associated with northern political elites and, during its founder's lifetime, seen as sympathetic to centre-right and APC-aligned positions."),
        "owner": "Leadership Newspaper Group (Leadership Media Group), founded by Sam Nda-Isaiah and now chaired by his wife Zainab Nda-Isaiah",
        "ownership_type": "private_corporate",
        "political_alignment": ("Long associated with northern political elites and centre-right positions, though post-2020 coverage includes broader, sometimes critical reporting of the current federal administration."),
        "source_urls": ["https://en.wikipedia.org/wiki/Leadership_(newspaper)", "https://leadership.ng/about-us/"],
    },
    {
        "name": "Nairametrics",
        "ownership_lean": "independent",
        "regional_base": "south_west",
        "confidence": "medium",
        "notes": ("Lagos-based business and markets site known for explainers on macro-economy, personal finance and Nigerian equities; coverage often highlights policy impacts on investors and consumers."),
        "owner": "Nairametrics Financial Advocacy Ltd, founded by Ugochukwu 'Ugodre' Obichie (Ugo Obi-Chukwu)",
        "ownership_type": "private_corporate",
        "political_alignment": ("Positions itself as a non-partisan financial and economic analysis platform with no overt alignment to APC or PDP, though it frequently critiques government economic policy from a data-driven perspective."),
        "source_urls": ["https://nairametrics.com/about-nairametrics/", "https://en.wikipedia.org/wiki/Nairametrics"],
    },
    {
        "name": "The Whistler",
        "ownership_lean": "mixed",
        "regional_base": "national",
        "confidence": "low",
        "notes": ("Abuja-based online outlet whose coverage includes both critical investigations into public spending and governance and relatively favourable reporting on government programmes."),
        "owner": "The Whistler Communications Ltd, founded and published by James Ume",
        "ownership_type": "private_corporate",
        "political_alignment": ("No clear party line; alternating watchdog and establishment-friendly tones."),
        "source_urls": ["https://thewhistler.ng/about-us/", "https://thewhistler.ng/"],
    },
    {
        "name": "Financial Watch",
        "ownership_lean": "mixed",
        "regional_base": "south_west",
        "confidence": "low",
        "notes": ("Online business and economy platform that publishes corporate press releases, regulatory updates and consumer-focused stories on banks and telecoms."),
        "owner": "Financial Watch Services Ltd (Financial Watch Nigeria), founded by entrepreneur and publisher Omolara Philips",
        "ownership_type": "private_corporate",
        "political_alignment": ("No consistent, publicly recognised partisan alignment; generally treated as a niche business-news site."),
        "source_urls": ["https://financialwatchngr.com/about-us/", "https://financialwatchngr.com/"],
    },
    {
        "name": "Blueprint",
        "ownership_lean": "pro_government",
        "regional_base": "north",
        "confidence": "medium",
        "notes": ("Abuja-based daily perceived as friendly to northern political interests and, given its owner's current role in the APC-led federal government, generally seen as sympathetic to the present administration's narratives."),
        "owner": "Blueprint Newspapers Limited, promoted by Mohammed Idris Malagi (media entrepreneur and current Nigerian Minister of Information)",
        "ownership_type": "private_corporate",
        "political_alignment": ("Given Mohammed Idris's current role in the APC-led federal government, generally seen as sympathetic to the present administration's narratives."),
        "source_urls": ["https://blueprint.ng/about-us/", "https://www.medianigeria.com/owners-of-top-news-papers-in-nigeria/"],
    },
    {
        "name": "BBC Sport",
        "ownership_lean": "independent",
        "regional_base": "national",
        "confidence": "high",
        "notes": ("Sports-only section of the BBC's global service that focuses on sporting events and organisations rather than party politics."),
        "owner": "British Broadcasting Corporation (BBC), a UK public corporation funded primarily by the television licence fee",
        "ownership_type": "private_nonprofit",
        "political_alignment": ("No meaningful partisan alignment; broader BBC journalism aims at impartiality under a public-service mandate."),
        "source_urls": ["https://www.bbc.com/sport", "https://www.bbc.co.uk/aboutthebbc"],
    },
    {
        "name": "Premium Times Sports",
        "ownership_lean": "independent",
        "regional_base": "national",
        "confidence": "medium",
        "notes": ("Sports desk of an investigative, non-partisan watchdog outlet; coverage focuses on Nigerian and international sports rather than party or governance issues."),
        "owner": "Premium Times Services Limited (same corporate owner as Premium Times Nigeria, founded by Dapo Olorunyomi)",
        "ownership_type": "private_corporate",
        "political_alignment": ("Not associated with any partisan political alignment; inherits parent organisation's standards for factual reporting."),
        "source_urls": ["https://www.premiumtimesng.com/about", "https://www.premiumtimesng.com/category/sports"],
    },
    {
        "name": "Sporting Life",
        "ownership_lean": "independent",
        "regional_base": "south_west",
        "confidence": "medium",
        "notes": ("Lagos-based sports site that emphasizes Nigerian and European football, betting-related information and transfer news."),
        "owner": "Complete Communications Ltd (publishers of Complete Sports and Sportinglife.ng), founded by Sunny Obazu-Ojeagbase",
        "ownership_type": "private_corporate",
        "political_alignment": ("No discernible partisan political alignment; fan-oriented sports platform rather than a political actor."),
        "source_urls": ["https://sportinglife.ng/about-us/", "https://www.medianigeria.com/owners-of-top-news-papers-in-nigeria/"],
    },
    {
        "name": "BBC News",
        "ownership_lean": "mixed",
        "regional_base": "national",
        "confidence": "high",
        "notes": ("One of the world's largest and most trusted international news providers with strong editorial standards and extensive foreign bureaus."),
        "owner": "British Broadcasting Corporation (BBC), a UK public corporation funded primarily by the television licence fee",
        "ownership_type": "private_nonprofit",
        "political_alignment": ("Formally committed to impartiality but perceived centrist/pro-establishment tilt in UK politics; international coverage aims for objectivity."),
        "source_urls": ["https://www.bbc.com/news", "https://www.bbc.co.uk/aboutthebbc"],
    },
    {
        "name": "Al Jazeera",
        "ownership_lean": "mixed",
        "regional_base": "national",
        "confidence": "high",
        "notes": ("Doha-based global network praised for in-depth coverage of the Global South and conflict zones; strong investigative tradition."),
        "owner": "Al Jazeera Media Network, owned by the government of Qatar through the Qatar Media Corporation",
        "ownership_type": "state_government",
        "political_alignment": ("Reflects Qatari foreign-policy perspectives with critical coverage of many Western-aligned governments but cautious treatment of Qatar; centre-left factual rating."),
        "source_urls": ["https://www.aljazeera.com/aboutus", "https://en.wikipedia.org/wiki/Al_Jazeera"],
    },
    {
        "name": "MyJoyOnline",
        "ownership_lean": "mixed",
        "regional_base": "national",
        "confidence": "medium",
        "notes": ("Accra-based platform combining radio, TV and online news; widely influential private media house in Ghana."),
        "owner": "The Multimedia Group Limited (Ghana), founded by Kwasi Twum",
        "ownership_type": "private_corporate",
        "political_alignment": ("Perceived as robust and sometimes critical of ruling governments (currently NPP-led), while also providing space for establishment voices."),
        "source_urls": ["https://www.myjoyonline.com/about-us/", "https://en.wikipedia.org/wiki/Multimedia_Group_Limited"],
    },
    {
        "name": "Standard Digital",
        "ownership_lean": "mixed",
        "regional_base": "national",
        "confidence": "medium",
        "notes": ("One of Kenya's oldest media houses with cross-platform presence (print, TV, online); historically linked to establishment interests."),
        "owner": "The Standard Group PLC, listed on the Nairobi Securities Exchange with major shareholding linked historically to the Moi family and other Kenyan elites",
        "ownership_type": "private_corporate",
        "political_alignment": ("Long associated with establishment and former ruling-party interests, though recent years include diverse voices and both critical and pro-government content."),
        "source_urls": ["https://www.standardmedia.co.ke/corporate/article/2000190109/about-standard-group-plc", "https://en.wikipedia.org/wiki/Standard_Group"],
    },
]

BEFORE = supabase.table("source_bias").select("count", count="exact").execute().count
print(f"source_bias rows before: {BEFORE}")

rows = []
for p in PROFILES:
    name = p["name"]
    res = supabase.table("sources").select("id").eq("name", name).execute()
    if not res.data:
        print(f"  ! WARN: no sources row named '{name}' — skipping")
        continue
    sid = res.data[0]["id"]
    rows.append({
        "source_id": sid,
        "ownership_lean": p["ownership_lean"],
        "regional_base": p["regional_base"],
        "confidence": p["confidence"],
        "notes": p["notes"],
        "owner": p["owner"],
        "ownership_type": p["ownership_type"],
        "political_alignment": p["political_alignment"],
        "source_urls": p["source_urls"],
    })

if not rows:
    sys.exit("No rows to upsert (all sources missing?). Aborting.")

out = supabase.table("source_bias").upsert(rows).execute()
print(f"upserted {len(rows)} rows (status: {getattr(out, 'status_code', 'n/a')})")

AFTER = supabase.table("source_bias").select("count", count="exact").execute().count
print(f"source_bias rows after: {AFTER}")

# Spot-check
sample = supabase.table("source_bias").select(
    "source_id, ownership_lean, regional_base, owner, source_urls"
).limit(3).execute()
for r in sample.data:
    print("  ", r["ownership_lean"], "|", r["regional_base"], "|", r["owner"][:40])
print("done.")
