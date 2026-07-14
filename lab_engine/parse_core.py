#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер лабораторных PDF-анализов Валентина.
Вход:  <healthcare>/.txt_cache/*.txt — извлечённые pdftotext
Выход: <healthcare>/scripts/parsed.json
"""

import os, re, json
from datetime import datetime
from pathlib import Path

HEALTHCARE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = HEALTHCARE_DIR / ".txt_cache"
OUT = HEALTHCARE_DIR / "scripts" / "parsed.json"

# --- Нормализация имён биомаркеров ---
# Ключ — каноническое русское имя. Значения — синонимы (lower-case, без знаков).
SYNONYMS = {
    # Общий анализ крови
    "Гемоглобин": ["гемоглобин", "hb", "haemoglobin", "hemoglobin", "hämoglobin", "гемоглобин(hb)"],
    "Эритроциты": ["эритроциты", "rbc", "erythrocytes", "erythrozyten"],
    "Гематокрит": ["гематокрит", "hct", "haematocrit", "hematocrit", "hämatokrit"],
    "MCV": ["mcv", "среднийобъемэритроцита", "среднийобъёмэритроцита", "meancellvolume"],
    "MCH": ["mch", "среднеесодержаниеhbвэритроците", "ср.содерж.hbвэритроците", "meancellhaemoglobin", "meancellhemoglobin",
            "среднее объемное содержание гемоглобина в эритроците", "среднее объемное содержание"],
    "MCHC": ["mchc", "средняяконцентрацияhbвэритроците", "ср.конц.hbв1эритроц", "средняяконцентрациягемоглобинавэритроците", "средняяконцентрациягемоглобина", "meancellhaemoglobinconc", "meancellhemoglobinconcentration"],
    "RDW": ["rdw", "rdw-cv", "гетерогенностьэритроцитовпообъему", "гетерогенностьэритроцитовпообъёму",
            "анизоцитоз эритроцитов"],
    "RDW-SD": ["rdw-sd", "гетерогенность эритроцитов по объему (rdw-sd)"],
    "Лейкоциты": ["лейкоциты", "wbc", "leucocytes", "leukocytes", "leukozyten"],
    "Тромбоциты": ["тромбоциты", "plt", "thrombocytes", "platelets", "thrombozyten"],
    "MPV": ["mpv", "среднийобъемтромбоцитов", "среднийобъёмтромбоцитов", "среднийобъемтромбоцита"],
    "PCT": ["pct", "тромбокрит"],
    "PDW": ["pdw", "гетерогенностьтромбоцитовпообъему"],
    "Лимфоциты %": ["лимфоциты%", "lymphocytes%", "лимфоцитыпроцент"],
    "Лимфоциты": ["лимфоциты(lym)", "lym", "lymphocytes"],
    "Моноциты %": ["моноциты%", "monocytes%"],
    "Моноциты": ["моноциты(mon)", "mon", "monocytes"],
    "Нейтрофилы %": ["нейтрофилы%", "segmentedneutrophils%"],
    "Нейтрофилы": ["нейтрофилы(neu)", "neu", "neutrophils", "segmentedneutrophils", "сегментоядерные"],
    "Эозинофилы %": ["эозинофилы%", "eosinophils%"],
    "Эозинофилы": ["эозинофилы(eos)", "eos", "eosinophils"],
    "Базофилы %": ["базофилы%", "basophils%"],
    "Базофилы": ["базофилы(bas)", "bas", "basophils"],
    "Большие неокрашенные клетки (LUC)": ["largeunstainedcells", "luc"],
    "СОЭ": ["соэ", "esr", "westergren", "esrwestergren1hour", "соэ(повестергрену)"],
    # ОАК — расширенные параметры (Sysmex)
    "Ретикулоциты абс.": ["абсолютноеколичестворетикулоцитов", "ретикулоцитыабс"],
    "Ретикулоциты %": ["относительноеколичестворетикулоцитов", "ретикулоциты,отн.(ret%)", "ретикулоцитыотнret%", "ret%", "reticulocytes%"],
    "Незрелые ретикулоциты": ["незрелыеретикулоциты", "immaturereticulocytes"],
    "Фракция незрелых ретикулоцитов (IFR)": ["фракциянезрелыхретикулоцитов", "фракциянезрелыхретикулоцитов(ifr)", "ifr"],
    "Микроциты (MicroR)": ["микроциты(micror)", "микроциты", "microcytes", "micror"],
    "Макроциты (MacroR)": ["макроциты(macror)", "макроциты", "macrocytes", "macror"],
    "Нормобласты (NRBC)": ["нормобласты (ядросодержащие эритроциты) (nrbc)", "нормобласты", "nrbc"],
    "Метамиелоциты": ["метамиелоциты", "metamyelocytes"],
    "Миелоциты": ["миелоциты", "myelocytes"],
    "Плазматические клетки (AS-LYMP)": ["плазматическиеклетки(as-lymp)", "плазматическиеклетки", "as-lymp", "aslymp"],
    "Коэффициент крупных тромбоцитов (P-LCR)": ["коэффициентчислакрупныхтромбоцитов(p-lcr)", "p-lcr", "plcr", "коэффициенткрупныхтромбоцитов"],
    "Реактивность нейтрофилов (NEUT-RI)": ["интенсивностьреактивностинейтрофилов(neut-ri)", "neut-ri", "neutri"],
    "Гранулярность нейтрофилов (NEUT-GI)": ["показательгранулярностинейтрофилов(neut-gi)", "neut-gi", "neutgi"],

    # Биохимия
    "Креатинин": ["креатинин", "creatinine", "kreatinin"],
    "Мочевина": ["мочевина", "urea", "harnstoff"],
    "Мочевая кислота": ["мочеваякислота", "uricacid", "harnsäure"],
    "Цистатин С": ["цистатин с", "цистатинс", "цистатин-с", "cystatin c", "cystatinc"],
    "АЛТ": ["алт", "alt", "alanineaminotransferase", "alanineamonitransferase", "аланинаминотрансфераза", "аланинаминотрансфераза(алт)"],
    "АСТ": ["аст", "ast", "aspartateaminotransferase", "aspartate-aminotransferase", "аспартатаминотрансфераза", "аспартатаминотрансфераза(аст)"],
    "Билирубин общий": ["билирубинобщий", "totalbilirubin", "bilirubintotal", "gesamtbilirubin"],
    "Билирубин прямой": ["билирубинпрямой", "directbilirubin", "bilirubindirekt"],
    "Билирубин непрямой": ["билирубиннепрямой", "indirectbilirubin"],
    "Щелочная фосфатаза": ["щелочнаяфосфатаза", "фосфатазащелочная", "alp", "alkalinephosphatase", "alkalischephosphatase"],
    "ГГТ": ["ггт", "ggt", "гамма-гт", "гаммагт", "gamma-gt", "gammagt", "gamma-glutamyltransferase", "gammaglutamyletransferase", "gammaglutamyltransferase"],
    "ЛДГ": ["лдг", "ldh", "lactatedehydrogenase", "лактатдегидрогеназа", "лактатдегидрогеназа(лдг)"],
    "Альфа-амилаза": ["альфаамилаза", "альфа-амилаза", "alphaamylase", "amylase"],
    "Амилаза панкреатическая": ["амилазапанкреатическая", "панкреатическаяамилаза", "pancreaticamylase"],
    "Липаза": ["липаза", "lipase"],
    "Креатинфосфокиназа (КФК)": ["креатинфосфокиназа", "creatinekinase", "creatinkinase", "ck", "кфк"],
    "Креатинкиназа МВ (КФК-МВ)": ["креатинфосфокиназаmb", "креатинкиназамв", "ck-mb", "ckmb", "кфкмв", "кфк-мв"],
    "Паратгормон (ПТГ)": ["паратирин", "паратирин(паратгормон)", "паратгормон", "parathyroidhormone", "pth"],
    "Ревматоидный фактор": ["ревматоидныйфактор", "rheumatoidfactor", "rf"],
    "Общий белок": ["общийбелок", "totalprotein", "gesamteiweiss"],
    "Альбумин": ["альбумин", "albumin"],
    "Глобулины": ["глобулины", "globulin"],
    "Холестерин общий": ["холестеринобщий", "общийхолестерин", "totalcholesterol", "cholesterol", "cholesterin"],
    "Холестерин ЛПНП": ["холестерин-лпнп", "ldlcholesterol", "cholesterolldl", "ldl-cholesterin", "холестеринлпнп"],
    "Холестерин ЛПВП": ["холестерин-лпвп", "hdlcholesterol", "cholesterolhdl", "hdl-cholesterin", "холестеринлпвп"],
    "Холестерин ЛПОНП": ["холестерин-лпонп", "vldlcholesterol", "cholesterolvldl", "холестеринлпонп"],
    "Триглицериды": ["триглицериды", "triglycerides", "triglyceride"],
    "Индекс атерогенности": ["индексатерогенности", "atherogenicindex"],
    "Аполипопротеин A1": ["аполипопротеинa1", "аполипопротеинa-1", "apolipoproteina1", "apoa1", "аполипопротеинa", "аполипопротеинa-1"],
    "Аполипопротеин B": ["аполипопротеинb", "apolipoproteinb", "apob", "аполипопротеинв"],  # русская В тоже
    "Липопротеин (а)": ["липопротеин(а)", "липопротеина", "lipoproteina", "lp(a)"],
    "Глюкоза": ["глюкоза", "glucose", "glukose"],
    "Гликированный гемоглобин": ["гликированныйгемоглобин", "гликозилированныйhb", "hba1c", "hba1%", "hba1%(гликированныйгемоглобин)", "гликозилированный гемоглобин (hba1c)", "гликозилированный гемоглобин"],
    "Инсулин": ["инсулин", "insulin"],
    "C-пептид": ["c-пептид", "с-пептид", "c-peptide", "cpeptid", "cpeptide"],
    "Лептин": ["лептин", "leptin"],
    "HOMA-IR": ["homa-ir", "homair", "homa", "indexhoma"],
    "Гомоцистеин": ["гомоцистеин", "homocysteine"],
    "С-реактивный белок": ["с-реактивныйбелок", "c-reactiveprotein", "crp", "с-реактивныйбелок(вч)", "hsCRP", "с-реактивный белок(высокочувствительный)", "с-реактивныйбелок(высокочувствительный)"],

    # Электролиты и микроэлементы
    "Натрий": ["натрий", "sodium", "natrium", "na", "натрий(na+)", "натрийna+"],
    # NT-proBNP содержит «натрий» в имени — отдельный канон с приоритетом
    "NT-proBNP": ["nt-probnp", "ntprobnp", "натрийуретическийпептид", "n-концевойнатрийуретическийпептид",
                   "n-концевойпропептиднатрийуретическогогормона",
                   "n-концевойпропептидмозговогонатрийуретическогогормона",
                   "мозговойнатрийуретическийгормон", "мозговойнатрийуретическийгормон(nt-probnp)",
                   "натрийуретическийгормон"],
    "Калий": ["калий", "potassium", "kalium", "k"],
    "Хлор": ["хлор", "chloride", "chlorid"],
    "Кальций": ["кальций", "calcium", "ca"],
    "Кальций ионизированный": ["кальцийионизированный", "ionizedcalcium", "ca(ионизированный)", "caионизированный"],
    "Кальцитонин": ["кальцитонин", "calcitonin"],
    "Магний": ["магний", "magnesium", "mg", "магний,mg"],  # сыворотка по умолчанию
    "Магний (ИСП-МС)": ["магнийиспмс", "магнийэритроциты"],  # для разделения по unit
    "Фосфор": ["фосфор", "phosphorus", "phosphor"],
    "Железо": ["железо", "iron", "eisen", "fe", "сывороточноежелезо"],
    "Цинк": ["цинк", "zinc", "zink", "zn", "цинк,zn"],
    "Цинк (ИСП-МС)": ["цинкиспмс", "цинкэритроциты", "цинкволосы"],
    "Селен": ["селен", "selenium", "selen", "se"],
    "Селен (ИСП-МС)": ["селениспмс"],
    "Цинк (волосы)": ["цинкволосы"],
    "Медь": ["медь", "copper", "cu"],
    "Медь (ИСП-МС)": ["медьиспмс"],

    # Метаболизм железа
    "Ферритин": ["ферритин", "ferritin"],
    "Трансферрин": ["трансферрин", "transferrin"],
    "ОЖСС": ["ожсс", "tibc", "общаяжелезосвязывающаяспособность", "общаяжелезосвязывающаяспособность(ожсс)"],
    "Латентная ЖСС": ["латентнаяжсс", "uibc"],
    "Растворимые рецепторы трансферрина (sTfR)": ["растворимые рецепторы трансферрина (stfr)", "растворимый рецептор трансферрина", "растворимыерецепторытрансферрина", "stfr"],

    # Витамины
    "Витамин B12": ["витаминb12", "vitaminb12", "b12", "цианокобаламин"],
    "Голотранскобаламин (активный B12)": ["активныйвитаминав12", "активныйb12", "holotranscobalamin", "holotc"],
    "Витамин B5 (пантотеновая кислота)": ["витаминb5", "пантотеноваякислота", "vitaminb5", "pantothenicacid"],
    "Бета-каротин": ["бета-каротин", "бетакаротин", "betacarotene"],
    "Коэнзим Q10": ["коэнзимq10", "коэнзимq10(убихинон)", "убихинон", "ubiquinone", "coenzymeq10"],
    "Глутатион (восстановленный)": ["глутатион", "глутатион(восстановленный)", "glutathione"],
    "Малоновый диальдегид": ["малоновыйдиальдегид", "malondialdehyde", "mda"],
    "8-ОН-дезоксигуанозин": ["8-он-дезоксигуанозин", "8ондезоксигуанозин", "8-oh-deoxyguanosine", "8-ohdg"],
    "Витамин D (25-OH total)": ["витаминd", "25-ohвитаминd", "25-ohвитаминd(25-гидроксикальциферол)", "25-ohvitamind", "25(oh)d", "vitamind", "25-гидроксикальциферол",
                   "витаминd25-он", "витаминd25-онкальциферол", "витаминd(25-он)(кальциферол)", "витаминd25онкальциферол",
                   "витаминd(25-он)кальциферол"],
    "Витамин D2 (эргокальциферол)": ["витаминd2", "витаминd2(эргокальциферол)", "эргокальциферол", "vitamind2", "ergocalciferol"],
    "Витамин D3 (холекальциферол)": ["витаминd3", "витаминd3(холекальциферол)", "холекальциферол", "vitamind3", "cholecalciferol"],
    "Фолиевая кислота": ["фолиеваякислота", "folicacid", "folsäure", "folat"],
    "Фолиевая кислота в эритроцитах": ["фолиеваякислотавэритроцитах", "folateinredbloodcells", "rbcfolate"],
    "Витамин A": ["витаминa", "vitamina", "ретинол"],
    "Витамин E": ["витаминe", "vitamine", "токоферол"],
    "Витамин K": ["витаминk", "vitamink"],
    "Витамин B1": ["витаминb1", "тиамин", "thiamine"],
    "Витамин B6": ["витаминb6", "пиридоксин", "pyridoxine"],
    "Витамин C": ["витаминc", "vitaminc", "аскорбиноваякислота"],

    # Гормоны - щитовидная
    "ТТГ": ["ттг", "tsh", "tshbasal", "тиреотропныйгормон"],
    "Т3 свободный": ["т3свободный", "ft3", "т3св"],
    "Т4 свободный": ["т4свободный", "ft4", "т4св"],
    "Т3 общий": ["т3общий", "t3"],
    "Т4 общий": ["т4общий", "t4"],
    "Анти-ТПО": ["анти-тпо", "anti-tpo", "tpoab", "антителакпероксидазе"],
    "Анти-ТГ": ["анти-тг", "anti-tg", "tgab", "ат-тг", "антитела к тиреоглобулину", "антитела к тиреоглобулину, анти-тг"],
    "Тиреоглобулин": ["тиреоглобулин", "thyroglobulin"],
    "Антитела к рецепторам ТТГ": ["антитела к рецепторам ттг", "trab"],

    # Половые гормоны
    "Тестостерон общий": ["тестостерон", "testosterone", "тестостеронобщий", "totaltestosterone"],
    "Тестостерон свободный": ["тестостеронсвободный", "freetestosterone"],
    "Тестостерон в слюне": ["тестостеронвслюне", "salivarytestosterone"],
    "Эстрадиол": ["эстрадиол", "estradiol", "e2"],
    "ЛГ": ["лг", "lh", "luteinizinghormone", "лютеинизирующийгормон", "лютеинизирующийгормон(лг)"],
    "ФСГ": ["фсг", "fsh", "follicle-stimulatinghormone", "фолликулостимулирующийгормон", "фолликулостимулирующийгормон(фсг)"],
    "Пролактин": ["пролактин", "prolactin"],
    "ДГЭА-С": ["дгэа-с", "dheas", "dhea-s"],
    "ДГЭА": ["дгэа", "dhea"],
    "Прогестерон": ["прогестерон", "progesterone"],
    "ГСПГ": ["гспг", "shbg", "sexhormonebindingglobulin", "sexhormonebindingglobulin.",
             "глобулинсвязывающийполовые", "глобулинсвязывающийполовыегормоны",
             "секс-гормонысвязглобулин", "секс-гормонысвязанглобулин"],
    "Кортизол": ["кортизол", "cortisol"],
    # Стероидные гормоны (метод ВЭЖХ-МС/МС)
    "17-ОН-прегненолон": ["17-он-прегненолон", "17онпрегненолон", "17-oh-pregnenolone", "17-hydroxypregnenolone"],
    "17-ОН-прогестерон": ["17-он-прогестерон", "17онпрогестерон", "17-oh-progesterone", "17-hydroxyprogesterone"],
    "Прегненолон": ["прегненолон", "pregnenolone"],
    "Андростендион": ["андростендион", "androstenedione"],
    "Кортизон": ["кортизон", "cortisone"],

    # Кардио / гемостаз
    "АЧТВ": ["ачтв", "aptt", "ачтвактивированноечастичноетромбопластиновое",
              "ачтв(активированноечастичноетромбопластиновоевремя)",
              "активированноечастичноетромбопластиновоевремя"],
    "Протромбиновое время": ["протромбиновоевремя", "pt"],
    "Протромбин по Квику": ["протромбинпоквику", "quick"],
    "Протромбиновый индекс": ["протромбиновыйиндекс", "пти", "pti"],
    "Антитромбин III": ["антитромбиниii", "антитромбин3", "antithrombiniii", "atiii"],
    "Тромбиновое время": ["тромбиновоевремя", "thrombintime", "tt"],
    "РФМК": ["рфмк", "рфмк(растворимыефибрин-мономерныекомплексы)", "soluablefibrinmonomercomplexes"],
    "МНО": ["мно(международноенормализованное", "мно(международноенормализованноеотношение)"],
    "Волчаночный антикоагулянт": ["волчаночныйантикоагулянт", "lupusanticoagulant", "la"],
    "МНО": ["мно", "inr", "intnormalizedratio", "intnormalizedratio(inr)", "международноенормализованноеотношение"],
    "Протромбин (Quick)": ["prothrombin", "quick", "протромбинпоквику"],
    "Фибриноген": ["фибриноген", "fibrinogen"],
    "D-димер": ["d-димер", "ddimer", "d-dimer"],
    "Тропонин": ["тропонин", "troponin"],
    "BNP": ["bnp"],
    # NT-proBNP — расширенный канон уже выше, в секции электролитов

    # COVID
    "РНК SARS-CoV-2": ["рнкsars-cov-2", "rnasars-cov-2", "covid-19пцр", "sars-cov-2real-timeпцр"],
    "Антитела SARS-CoV-2 IgG": ["антителаsars-cov-2igg", "anti-sars-cov-2igg", "covid-19igg", "covid-19антителаigg",
                                  "антителакsars-cov2covid-19igg", "antibodiestosars-cov2covid-19igg"],
    "Антитела SARS-CoV-2 IgM": ["антителаsars-cov-2igm", "covid-19igm",
                                 "антителакsars-cov2covid-19igm", "antibodiestosars-cov2covid-19igm"],
    "Антитела SARS-CoV-2 IgA": ["антителакsars-cov2covid-19iga", "antibodiestosars-cov2covid-19iga",
                                 "антителакs1белкувирусаsars-cov2covid-19iga"],
    "Антитела SARS-CoV-2 RBD": ["антителаsars-cov-2rbd", "rbd",
                                  "антителаiggккоронавирусуsars-cov-2-rbd",
                                  "антителаiggккоронавирусуsars-cov-2rbd",
                                  "антителаигккоронавирусуsars-cov-2rbd",
                                  "количественноеопределениеантителкrbdдоменуs1белкавирусаsars-cov-2igg",
                                  "antibodiestotherbddomains1ofproteinsars-cov-2igg",
                                  "quantitationantibodiestorbddomains1oftheproteinsars-cov-2igg",
                                  "sars-cov-2-rbd-ифа-гамалеи", "sars-cov-2-rbd-ифагамалеи",
                                  "sars-cov2igg(abbottlaboratorieseu", "sars-cov2igg(abbottlaboratories",
                                  "определениеиндексаавидностиантителк",
                                  "определениеиндексаавидностиантителкrbdдоменувирусаsars-cov-2igg"],
    "Антитела SARS-CoV-2 S1 (УЕ)": ["антителакs1белкувирусаsars-cov2", "антителакs1белкуви русаsars-cov2",
                                      "антителакs1белкувирусаsars-cov2covid-19"],
    "Антитела SARS-CoV-2 N (нуклеокапсид) IgG": ["антителакнуклеокапсидномубелкувирусаsars-cov-2igg",
                                                    "antibodiestonucleocapsidproteinsars-cov-2igg",
                                                    "антителакнуклеокапсидному"],
    "Антитела SARS-CoV-2 S1 IgG (Euroimmun)": ["антителакs1белкувирусаsars-cov2covid-19iggeuroimmun",
                                                  "антителакs1белкувирусаsars-cov2covid-19igg"],
    "Омега-3 индекс": ["омега-3индекс", "омега3индекс", "omega-3index"],
    "CAG-повторы (ген AR)": ["cag-повторы", "cagповторы", "andrareceptorcagrepeats", "ar(cag)nrepeat"],

    # Инфекции
    "Anti-HIV": ["anti-hiv-1/hiv-2", "anti-hiv", "hiv", "hiv1p24",
                 "антителаквич12типа,антигенp24", "антителаквич1,2типа,антигенp24",
                 "антителаквич12типаантигенp24"],
    "HBsAg": ["hbsag", "поверхностныйантигенвирусагепатитав", "поверхностныйантигенвирусагепатитав,"],
    "Anti-HBs (антитела к HBsAg)": ["anti-hbs", "antihbs", "антителакповерхностномуантигенувирусагепатитавanti-hbs",
                                     "антителакповерхностномуантигенувирусагепатитав"],
    "Anti-HCV IgG": ["anti-hcvigg", "антителаквирусугепатитаc,anti-hcv", "антителаквирусугепатитасanti-hcv"],
    "Anti-HSV": ["herpessimplexvirusi,ii", "herpessimplexviruii", "антителаквирусупростогогерпеса",
                  "антителаквирусупростогогерпеса1и2типовlggherpessimplexvirusi/iiigg",
                  "антителаквирусупростогогерпеса1и2типовlgghsvigg",
                  "типовlgg,hsvigg", "hsvigg", "антителаквирусупростогогерпеса1и2типов"],
    "Anti-Rubella IgG": ["антителаквирусукраснухиlgg", "антителаквирусукраснухиlgg,rubellaigg",
                          "антителаквирусукраснухиigg", "rubellaigg", "anti-rubella"],
    "Anti-Mumps IgG": ["антителаквирусупаротитаlgg", "антителаквирусупаротитаlgg,mumpsigg",
                       "антителаквирусупаротитаigg", "mumpsigg", "anti-mumps"],
    "Anti-Mycoplasma genitalium": ["myсoplasmagenitalium", "mycoplasmagenitalium"],
    "Anti-Mycoplasma hominis": ["myсoplasmahominis", "mycoplasmahominis"],
    "Anti-Candida IgG": ["антителаккандидеlggcandidaalbigg", "антителаккандиде", "candidaalb.igg"],
    "Anti-M.tuberculosis": ["микобактериитуберкулеза,m.tuberculosis", "микобактериитуберкулезаmtuberculosis"],
    "Anti-Varicella-Zoster IgG": ["антителаквирусуварицелла-зостерlgg,", "антителаквирусуварицеллазостерlgg",
                                   "varicella-zostervirusigg", "vziggg", "anti-vzv",
                                   "антителаквирусуварицеллазостерigg",
                                   "антителаквирусуварицелла-зостеригг",
                                   "varicellazosterigg", "varicella-zosterigg",
                                   "антителаквирусуварицелла-зостеригg,varicellazosterigg"],
    "Anti-Measles IgG": ["антителаквирусукориlgg,measlesvirus", "антителаквирусукориlgg",
                         "measlesvirusigg", "anti-measles"],
    "Anti-Pertussis IgG": ["антителаквозбудителюкоклюшаigg,", "антителаквозбудителюкоклюшаigg",
                           "bordetellapertussisigg", "anti-pertussis"],
    "Anti-Diphtheria IgG": ["антителакдифтерийномуанатоксинуigg",
                             "антителакдифтерийномуанатоксинуiggdiphtheria",
                             "diphtheriatoxoidiggantibody", "anti-diphtheria",
                             "антителакдифтерийнойпалочкеcorynebacteriumdiphtheriae",
                             "антителакдифтерийнойпалочке",
                             "corynebacteriumdiphtheriae"],
    "Anti-Tetanus IgG": ["антителаквозбудителюстолбняка", "антителаквозбудителюстолбняка(clostridiumtetani)",
                          "tetanusiggantibody", "clostridiumtetaniigg"],
    "РНК вируса гриппа A H1N1": ["вирусгриппаa/h1n1(свинойгрипп),рнк", "вирусгриппаah1n1свинойгриппрнк", "h1n1pcr"],
    "Антиген вируса гриппа A": ["вирусгриппаа(антигенныйтест)", "вирусгриппааантигенныйтест", "fluaag"],
    "Антиген вируса гриппа B": ["вирусгриппаb(антигенныйтест)", "вирусгриппаbантигенныйтест", "flubag"],
    "Сифилис RPR": ["syphilisrpr"],
    "Anti-Toxoplasma IgG": ["антителактоксоплазмеlgg,toxoplasmaigg", "антителактоксоплазмеlgg",
                             "toxoplasmaigg", "anti-toxoplasma"],
    "Anti-CMV IgG": ["антителакцитомегаловирусуlgg,cmvigg", "антителакцитомегаловирусуigg",
                      "антителакцитомегаловирусуlgg", "cmvigg", "anti-cmv"],
    "Anti-Treponema pallidum": ["anti-trepon.pallidumtotaligg+igm", "anti-treponemapallidum",
                                "сифилис,антителакtreponema.pallidum", "сифилисантителактрепонема"],

    # Иммуноглобулины
    "IgA": ["iga"],
    "IgG": ["igg"],
    "IgM": ["igm"],
    "IgE": ["ige", "иммуноглобулинigeобщий", "иммуноглобулинlgeобщий"],

    # ПСА / онкомаркеры
    "ПСА общий": ["псаобщий", "psatotal", "totalpsa"],
    "ПСА свободный": ["псасвободный", "freepsa"],
    "Индекс свободного ПСА": ["индекссвободногопса", "freepsaindex"],
}

def _norm_syn(s: str) -> str:
    """Та же нормализация, что и для имени: lower + удаление пробелов/дефисов/скобок."""
    return re.sub(r"[\s\-\(\),\.\:;«»\"']+", "", s.lower()).replace("ё", "е")

# Развёрнутый поиск: lower → canonical (синонимы тоже нормализованы!)
SYN_LOOKUP = {}
for canon, syns in SYNONYMS.items():
    for s in syns:
        SYN_LOOKUP[_norm_syn(s)] = canon
    # сам ключ тоже
    SYN_LOOKUP[_norm_syn(canon)] = canon


# Подстрочный поиск — по убыванию длины (приоритет более специфичным синонимам)
SYN_LOOKUP_BY_LEN = sorted(SYN_LOOKUP.items(), key=lambda kv: -len(kv[0]))

def normalize_key(name: str) -> str:
    """Уменьшает имя биомаркера к каноническому виду."""
    s = name.strip()
    # уберём всякие лишние комментарии в скобках длиной > чем сам термин
    s = re.sub(r"\s+", " ", s)
    raw = re.sub(r"[\s\-\(\),\.\:;«»\"']+", "", s).lower()
    raw = raw.replace("ё", "е")
    if raw in SYN_LOOKUP:
        return SYN_LOOKUP[raw]
    # поищем подстрокой — длинные синонимы имеют приоритет
    for syn, canon in SYN_LOOKUP_BY_LEN:
        if len(syn) >= 4 and syn in raw:
            return canon
    return s.strip()


# --- Парсинг даты из имени файла ---
def extract_date(filename: str, text: str) -> str | None:
    """Сначала пытаемся взять дату из текста PDF (Взятие биоматериала),
    потом — из имени файла. Это правильнее: имя файла часто = дата выдачи
    результата, а не дата сдачи (могут отличаться на 1-2 дня)."""
    # Из текста — приоритет
    for pat in [
        r"Взятие биоматериала:\s*(\d{2})\.(\d{2})\.(\d{4})",
        r"Регистрация биоматериала:\s*(\d{2})\.(\d{2})\.(\d{4})",
        r"Регистрации биоматериала:\s*(\d{2})\.(\d{2})\.(\d{4})",
        r"DATE COLLECT:\s*(\d{2})\.(\d{2})\.(\d{4})",
        r"DATE:\s*(\d{2})\.(\d{2})\.(\d{4})",
        r"Дата взятия материала:\s*(\d{2})\.(\d{2})\.(\d{4})",
        # Другие РФ-лаборатории (МЕДСИ, Гемотест и т.п.)
        r"Дата получения результата:\s*(\d{2})\.(\d{2})\.(\d{4})",
        r"Дата выполнения исследования:\s*(\d{2})\.(\d{2})\.(\d{4})",
        r"Дата регистрации:\s*(\d{2})\.(\d{2})\.(\d{4})",
        # МЕДСИ: «Дата: DD.MM.YYYY» в шапке. Двоеточие сразу после «Дата», поэтому
        # «Дата рождения:» / «Дата печати результата:» сюда не попадают.
        r"\bДата:\s*(\d{2})\.(\d{2})\.(\d{4})",
    ]:
        m = re.search(pat, text)
        if m:
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    # Fallback на имя файла
    name = filename
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", name)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.search(r"(\d{1,2})([A-Z]{3})(\d{4})", name)
    if m:
        mon = {"JAN":"01","FEB":"02","MAR":"03","APR":"04","MAY":"05","JUN":"06",
               "JUL":"07","AUG":"08","SEP":"09","OCT":"10","NOV":"11","DEC":"12"}.get(m.group(2))
        if mon:
            return f"{m.group(3)}-{mon}-{int(m.group(1)):02d}"
    return None


def detect_lab(filename: str, text: str) -> str:
    n = filename.lower()
    if "моситалмед" in n or "моситалмед" in text.lower():
        return "Моситалмед"
    if "dnkom" in n or "днком" in text.lower() or "ооо \"днком\"" in text.lower():
        return "DNKOM"
    if "emc" in n or "емц" in n or "scheskhpsr" in text.lower() or "european medical center" in text.lower() or "emcmos" in text.lower() or "поликлиника щепкина" in text.lower() or "поликлиника спиридоньевский" in text.lower():
        return "EMC"
    if "lansergof" in n or "marienstein" in text.lower() or "raubling" in text.lower():
        return "Lansergof / MVZ Raubling"
    if "niarmedic" in n.lower() or "ниармедик" in text.lower():
        return "Ниармедик"
    if "sputnik" in n.lower():
        return "Sputnik сертификат"
    if "covid19 vaccine" in n.lower():
        return "Сертификат вакцинации"
    if "dvs" in n.lower():
        return "DNKOM"
    if "bioimp" in n.lower():
        return "Биоимпеданс"
    if "сканирование" in n.lower():
        return "Скан (внешний документ)"
    return "Неизвестно"


def detect_type(filename: str, text: str) -> str:
    n = filename.lower()
    t = text.lower()
    types = []
    if "general blood" in n or "общий анализ крови" in t or "haematology" in t.lower():
        types.append("Общий анализ крови")
    if "biochem" in n or "биохимич" in t or "биохимия" in t:
        types.append("Биохимия")
    if "sugar" in n or "insulin" in n or "глюкоза" in t or "инсулин" in t and "homa" in t:
        types.append("Сахар/инсулин")
    if "cardio" in n or "холестерин" in t or "лпнп" in t:
        types.append("Липиды/кардио")
    if "endocrine" in n or "ттг" in t or "тестостерон" in t or "testosterone" in t.lower():
        types.append("Гормоны")
    if "hormone" in n or "гормон" in t:
        types.append("Гормоны")
    if "vitamin" in n.lower() or "витамин" in t:
        types.append("Витамины")
    if "creatinine" in n or "креатинин" in t:
        types.append("Креатинин")
    if "c-reactive" in n.lower() or "c reactive" in n.lower() or "с-реактивный" in t:
        types.append("CRP")
    if "d-dimer" in n.lower() or "d-димер" in t:
        types.append("D-димер")
    if "covid" in n.lower() or "sars-cov" in t.lower():
        types.append("COVID")
    if "rbd" in n.lower():
        types.append("COVID антитела")
    if "hiv" in n.lower() or "веneric" in n.lower() or "сифилис" in t or "syphilis" in t.lower() or "veneric" in n.lower():
        types.append("ИППП/инфекции")
    if "s-quality" in n.lower() or "spermogram" in t.lower() or "спермограмма" in t.lower() or "sperm" in t.lower():
        types.append("Спермограмма")
    if "tunel" in n.lower() or "tunnel" in n.lower() or "tunel assay" in t.lower():
        types.append("ДНК-фрагментация спермы")
    if "bioimp" in n.lower():
        types.append("Биоимпеданс")
    if "posev" in n.lower() or "посев" in t:
        types.append("Микробиология")
    if "herpes" in n.lower() or "kandida" in n.lower() or "candida" in t.lower():
        types.append("Инфекции")
    if "immune" in n.lower() or "иммун" in t:
        types.append("Иммунитет")
    if "heart" in n.lower():
        types.append("Кардио")
    if "vaccine" in n.lower() or "sputnik" in n.lower() or "вакцин" in t:
        types.append("Вакцинация")
    if "metabolism" in n.lower():
        types.append("Гормоны/метаболизм")
    if "mri" in n.lower() or "мрт" in t:
        types.append("МРТ")
    if "bau rus" in n.lower():
        types.append("Австрия — биохимия")
    return ", ".join(sorted(set(types))) if types else "Прочее"


# --- Парсинг параметров из текста ---

NUMBER = r"(?:[<>≥≤]?\s*\d{1,5}(?:[.,]\d+)?)"

# DNKOM-style: "Гемоглобин (Hb)    159,00    г/л    135,00 - 169,00"
# Чтобы избежать catastrophic backtracking, разделитель имя↔значение — минимум 2 пробела.
# Имя может начинаться с буквы или с цифры+дефис+буква (напр. «17-ОН-прегненолон», «25-OH витамин D»).
# Единица может иметь опциональный суффикс через пробел: «mg/L FEU», «10^9/л», «kg/m²».
# Имя может содержать `*` (Витамин D**), `;`, `+`.
RE_DNKOM = re.compile(
    r"^\s*((?:\d{1,2}[\-,])?[А-ЯЁA-Za-z][А-ЯЁA-Za-zа-яё0-9\-\(\),\./%№\*\+;]+(?:[ ][А-ЯЁA-Za-zа-яё0-9\-\(\),\./%№\*\+;]+)*?)\s{2,}"
    r"(?:[↑↓]\s*)?(?P<val>" + NUMBER + r")\s+"
    r"(?P<unit>[%А-ЯЁа-яёA-Za-z\^\d\-\*/]{1,30}(?:\s[A-Za-z]{2,6})?)?\s*"
    r"(?P<ref>(?:Мужчины:\s*)?(?:" + NUMBER + r"\s*[-–—]\s*" + NUMBER + r"|<\s*" + NUMBER + r"|>\s*" + NUMBER + r"|" + NUMBER + r"))?\s*\*?\s*$",
    re.MULTILINE
)

# Расслабленный DNKOM: ловит имя+значение+единицу, но без обязательного $ (после ref может быть произвольный текст).
# Используется ВТОРЫМ ПРОХОДОМ: только если RE_DNKOM не нашёл строку.
# Имя может начинаться и со строчной буквы (продолжение длинного имени со следующей строки).
RE_DNKOM_NOREF = re.compile(
    r"^\s*((?:\d{1,2}[\-,])?[А-ЯЁа-яёA-Za-z][А-ЯЁA-Za-zа-яё0-9\-\(\),\./%№\*\+;]+(?:[ ][А-ЯЁA-Za-zа-яё0-9\-\(\),\./%№\*\+;]+)*?)\s{2,}"
    r"(?:[↑↓]\s*)?(?P<val>" + NUMBER + r")\s+"
    r"(?P<unit>[%А-ЯЁа-яёA-Za-z\^\d/]{1,15}(?:\s[A-Za-z]{2,6})?)",
    re.MULTILINE
)

# EMC-style: "Haemoglobin    15.30    g/dL    (13.20-17.30)"
RE_EMC = re.compile(
    r"^\s*[A-D]?\s+([A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё0-9 \-\(\),\./%]{2,60}?)\s+"
    r"(?:[↑↓]\s*)?(?P<val>" + NUMBER + r")\s+"
    r"(?P<unit>[%A-Za-zА-ЯЁа-яё\^\d/\-]{1,20})\s*"
    r"\(\s*(?P<ref>" + NUMBER + r"\s*[-–—]\s*" + NUMBER + r"|<\s*" + NUMBER + r"|>\s*" + NUMBER + r")\s*\)\s*$",
    re.MULTILINE
)

# EMC qualitative-with-arrow: "C-Reactive Protein   ↑ 5.28   mg/L   (<5.00)"
# Для редких случаев когда нет (кода категории)
RE_EMC_LOOSE = re.compile(
    r"^\s+([A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё0-9 \-\(\),\./%]{2,60}?)\s+"
    r"[↑↓]\s*(?P<val>" + NUMBER + r")\s+"
    r"(?P<unit>[%A-Za-zА-ЯЁа-яё\^\d/\-]{1,20})\s*"
    r"\(\s*(?P<ref>[<>]?\s*" + NUMBER + r"(?:\s*[-–—]\s*" + NUMBER + r")?)\s*\)\s*$",
    re.MULTILINE
)

# EMC qualitative: "Anti-HIV-1/HIV-2    Negative"
RE_QUAL = re.compile(
    r"^\s*([A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё0-9 \-\(\),\./%]{2,80}?)\s+"
    r"(Negative|Positive|Not detected|обнаружено|не обнаружено|отриц[а-я]+|положит[а-я]+)\b",
    re.MULTILINE | re.IGNORECASE
)

# NIARMEDIC-style (unit ПЕРЕД value): "Имя ... ОП ... 3,905 ... 0,4- положительно"
# Колонки: Показатель | Ед.изм. | Результат | Референс
RE_NIARMEDIC = re.compile(
    r"^\s*([А-ЯЁA-Za-z][А-ЯЁA-Za-zа-яё0-9\-\(\),\./%]{2,80}?)\s{2,}"
    r"(?P<unit>[А-ЯЁа-яёA-Za-z\^\d/]{1,15})\s{2,}"
    r"(?P<val>" + NUMBER + r")\s+",
    re.MULTILINE
)

# Lansergof-style: "Sodium     142     mmol/l    133-150"
RE_LANSER = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9 \-\(\),\./%]{2,40}?)\s+"
    r"(?:[↑↓]\s*)?(?P<val>" + NUMBER + r")\s+"
    r"(?P<unit>[%A-Za-z\^\d/\-µ]{1,15})\s+"
    r"(?P<ref>" + NUMBER + r"\s*[-–—]\s*" + NUMBER + r")\s*$",
    re.MULTILINE
)


def is_garbage_name(name: str) -> bool:
    n = name.strip()
    if not n: return True
    if len(n) < 2: return True
    if re.search(r"\d{4}", n): return True  # дата в названии
    # Единица измерения внутри «имени» = это обрывок интерпретации/комментария, а не
    # маркер (реальные названия маркеров единиц не содержат). Ловит МЕДСИ-комментарии
    # вида «Холестерин 5,2-7,8 ммоль/л - нарушения…».
    if re.search(r"(ммоль|мкмоль|нмоль|пмоль|мкг/|нг/мл|пг/мл|г/дл|г/л|мг/дл|мг/л|"
                 r"ед/л|ме/мл|ме/л|мкме|10\^|мм/ч|мкмоль/л)\b", n.lower()):
        return True
    # точные мусорные ключи / слова, которые точно не биомаркеры
    bad_substr = ["page ", "result", "результ", "company", "patient", "doctor", "signature",
           "stamp", "date:", "time:", "age:", "sex:", "dob:", "lab.visit", "ф.и.о", "пол:",
           "дата рождения", "категория", "взятие", "регистрация", "заявка", "заказчик",
           "биоматериал", "исполнитель", "ё.и.о", "constituent", "untersuchung", "результаты одобрил",
           "исследования выполнены", "эякулят собран", "период воздержания",
           "no.", "ergebnis", "dimension", "endbefund", "barcode", "labor-nr",
           # обрывки заголовков (мульти-лайн ловит шапку таблицы)
           "инфекционная серология", "показатель", "infectious serology",
           "charge category", "biomaterial registration",
           "паспорт рф", "адрес регистрации",
           "рекомендации", "комментарии",
           # обрывки интерпретаций МЕДСИ (National Cholesterol Education Program и т.п.)
           "рекомендованные", "national cholesterol", "education program",
           "согласно рекомендациям", "диагностические критерии", "нарушения липид"]
    nl = n.lower()
    for b in bad_substr:
        if b in nl:
            return True
    # отдельные точные слова (не подстроки)
    bad_exact = {"стр.", "стр", "page", "code", "method", "dr"}
    if nl.strip(":") in bad_exact:
        return True
    return False


PCT_NAMES = {"Лимфоциты", "Моноциты", "Нейтрофилы", "Эозинофилы", "Базофилы"}

def adjust_pct(canon: str, unit: str) -> str:
    """Если для лейкоцитарной формулы единица % — пишем 'Маркер %'."""
    if canon in PCT_NAMES and unit and "%" in unit:
        return f"{canon} %"
    return canon


# Конверсии единиц для маркеров где разные лаборатории дают разные единицы
# (multiplier, target_unit)
UNIT_CONVERSIONS = {
    "Т4 свободный": {
        "ng/dl": (12.87, "пмоль/л"),
        "ng/dL": (12.87, "пмоль/л"),
    },
    "Т3 свободный": {
        "ng/l": (1.536, "пмоль/л"),
        "ng/L": (1.536, "пмоль/л"),
        "pg/ml": (1.536, "пмоль/л"),
    },
    "Тестостерон общий": {
        "ng/ml": (3.467, "нмоль/л"),  # ng/mL × 3.467 = нмоль/л
        "нг/мл": (3.467, "нмоль/л"),
    },
    "Эстрадиол": {
        "pg/ml": (3.671, "пмоль/л"),  # pg/mL × 3.671 = пмоль/л
        "пг/мл": (3.671, "пмоль/л"),
    },
}

def convert_unit(canon: str, value: str, unit: str):
    """Если для маркера известна конверсия — возвращает (new_value, new_unit). Иначе (value, unit)."""
    conv = UNIT_CONVERSIONS.get(canon, {})
    u = unit.strip().lower().replace(" ", "")
    for src, (mul, dst) in conv.items():
        if src.lower() == u:
            try:
                v = float(value.replace(",", ".").lstrip("<>≥≤"))
                new_v = round(v * mul, 2)
                return f"{new_v}", dst
            except ValueError:
                return value, unit
    return value, unit

def adjust_biomaterial(canon: str, unit: str, value: str = "") -> str:
    """Если маркер есть в нескольких биоматериалах с разными единицами/значениями,
    добавляем суффикс к имени. Применяется для микроэлементов."""
    u = unit.strip().lower()
    try: v = float(value.replace(",", ".").lstrip("<>≥≤"))
    except: v = None
    # Магний: сыворотка ммоль/л, ИСП-МС мг/л
    if canon == "Магний" and ("мг/л" in u or "mg/l" in u):
        return "Магний (ИСП-МС)"
    # Натрий/Калий/Кальций/Фосфор: сыворотка ммоль/л, ИСП-МС-панель мг/л
    if canon in ("Натрий", "Калий", "Кальций", "Фосфор") and ("мг/л" in u or "mg/l" in u):
        return f"{canon} (ИСП-МС)"
    # Цинк: сыворотка мкмоль/л, ИСП-МС мкг/л; волосы мкг/г — отдельный канон
    if canon == "Цинк":
        if "мкг/г" in u or "ug/g" in u:
            return "Цинк (волосы)"
        if "мкг/л" in u or "ug/l" in u:
            return "Цинк (ИСП-МС)"
    # Селен: сыворотка ~50–105 мкг/л (Lansergof), ИСП-МС ~23–190 мкг/л.
    # Различаем по значению: > 110 — почти наверняка ИСП-МС/волосы.
    if canon == "Селен" and v is not None and v > 110:
        return "Селен (ИСП-МС)"
    return canon


def _add_param(results, seen, name, val, unit, ref):
    """Унифицированное добавление параметра: канонизация + конвертация + фильтр мусора."""
    if is_garbage_name(name): return
    canon = normalize_key(name)
    canon = adjust_pct(canon, unit)
    canon = adjust_biomaterial(canon, unit, val)
    # Чисто-числовой unit (например, "31", "2910") — это шум от многострочной нормы
    if unit and re.match(r"^\d+([.,]\d+)?$", unit.strip()):
        return
    val_clean = val.replace(",", ".")
    value_conv, unit_conv = convert_unit(canon, val_clean, unit)
    key = (canon, value_conv.replace(" ", ""))
    if key in seen: return
    seen.add(key)
    results.append({
        "raw_name": name,
        "name": canon,
        "value": value_conv,
        "unit": unit_conv,
        "ref": ref,
    })


# Референс, стоящий отдельной строкой ПОД значением (ДНКОМ ВЭЖХ-МС: витамины и др.).
# «              2,1                4,3» — две колонки-порога, единиц нет.
_RE_REF_PAIR = re.compile(r"^\s{6,}(\d+(?:[.,]\d+)?)\s{2,}(\d+(?:[.,]\d+)?)\s*$")
_RE_REF_THRESH = re.compile(r"^\s{6,}([<>]\s*\d+(?:[.,]\d+)?)\s*$")
# Строка со значением+единицей (чтобы прицепить осиротевший референс к нужной строке).
_RE_HAS_VAL_UNIT = re.compile(
    r"\d+(?:[.,]\d+)?\s+(?:нг/мл|мкг/мл|пг/мл|пг/клетку|пмоль/л|нмоль/л|мкмоль/л|ммоль/л|"
    r"мкг/л|нг/л|ед/л|ме/мл|ме/л|мке/мл|г/л|г/дл|мг/л|мг/дл|мг/сут|%|фл|мм/ч|10\^\d+/л)")


def _merge_orphan_refs(text: str) -> str:
    """Приклеивает референс, стоящий отдельной строкой под значением, к строке значения.

    Формат ДНКОМ ВЭЖХ-МС (витамины): «Имя  значение  ед» на одной строке, а «low  high»
    (порог) — на следующей. Без этого референсы теряются. Инлайн-форматы не затрагивает
    (у них строк-«только-порогов» нет)."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        mp = _RE_REF_PAIR.match(ln)
        mt = None if mp else _RE_REF_THRESH.match(ln)
        if not mp and not mt:
            continue
        ref = f"{mp.group(1)} - {mp.group(2)}" if mp else mt.group(1)
        # найти ближайшую выше строку со значением+единицей без уже готового диапазона
        for j in range(i - 1, max(-1, i - 6), -1):
            if j < 0:
                break
            if _RE_HAS_VAL_UNIT.search(lines[j]) and not re.search(r"\d\s*[-–—]\s*\d", lines[j]):
                lines[j] = lines[j].rstrip() + "   " + ref
                lines[i] = ""
                break
    return "\n".join(lines)


def parse_text(text: str) -> list[dict]:
    """Извлекает список параметров (name, value, unit, ref)."""
    # Референсы отдельной строкой (ВЭЖХ-МС) — приклеить к строке значения ДО парсинга.
    text = _merge_orphan_refs(text)
    # DNKOM ОАК: вырезаем секцию «Микроскопическое исследование» — это ручная
    # лейкоцитарная микроскопия, дублирующая автоматическую лейкоформулу
    # (палочко-/сегментоядерные + плоские «Эозинофилы/Базофилы/...»). Режем от
    # заголовка секции до следующей секции (тромбоциты/СОЭ).
    text = re.sub(
        r"Микроскопическое исследование.*?(?=Тромбоцитарные параметр|Скорость оседания)",
        "", text, flags=re.DOTALL)
    results = []
    seen = set()  # (canonical_name, normalized_value) чтобы не дублировать

    # Все паттерны последовательно
    for regex, kind in [(RE_DNKOM, "dnkom"), (RE_EMC, "emc"), (RE_EMC_LOOSE, "emc_loose"), (RE_LANSER, "lanser")]:
        for m in regex.finditer(text):
            name = m.group(1).strip()
            val = m.group("val").strip()
            unit = (m.groupdict().get("unit") or "").strip()
            ref = (m.groupdict().get("ref") or "").strip()
            v = re.match(r"^[<>≥≤]?\s*(\d+([.,]\d+)?)$", val)
            if not v: continue
            _add_param(results, seen, name, val, unit, ref)

    # Многострочный DNKOM: имя на одной строке, значение и единица на следующей
    # (типичный кейс — длинные названия антител: «Антитела к дифтерийному анатоксину IgG»)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        ln = line.strip()
        # ищем строку, где почти весь контент — это значение + единица (нет ведущего имени слева)
        m = re.match(r"^\s{20,}(?P<val>" + NUMBER + r")\s+(?P<unit>[%А-ЯЁа-яёA-Za-z\^\d\-\*/]{1,30})(?:\s+(?P<ref>см\.\s*коммент|" + NUMBER + r"(?:\s*[-–—]\s*" + NUMBER + r")?))?", line)
        if not m: continue
        # имя — предыдущая непустая строка, начинающаяся с буквы.
        # Допускаем что в той строке есть цифры (например «<1,4 - отрицательно» — комментарий референса справа).
        # В таком случае имя — это часть строки до точки, где идут крупные пробелы перед цифрой.
        # Также пропускаем строки-комментарии референса (отступ >40 пробелов и начинаются с цифры/<).
        name_parts = []
        for j in range(i-1, max(-1, i-7), -1):
            if j < 0: break
            prev = lines[j].rstrip()
            ps = prev.strip()
            if not ps: continue
            # Пропускаем reference-comment строки: большой отступ и начинается с цифры/< или > или с пунктуации
            if re.match(r"^\s{30,}[<>]?\s*\d", prev):
                continue
            if not re.match(r"^[А-ЯЁа-яёA-Za-z]", ps): break
            # Отсечь правую часть после большого пробела (там может быть комментарий референса)
            cut = re.split(r"\s{4,}", prev.strip(), maxsplit=1)[0].strip()
            # Если после отсечения остались только цифры/спецсимволы — пропустить
            if not re.search(r"[А-ЯЁA-Za-zа-яё]", cut):
                break
            # Если cut слишком сильно состоит из цифр — это уже не имя
            digits = sum(1 for c in cut if c.isdigit())
            if digits > len(cut) // 2:
                break
            name_parts.insert(0, cut)
            # Сразу проверим: возможно это уже валидный канонический ключ — тогда не идём дальше
            if normalize_key(" ".join(name_parts)) != " ".join(name_parts).strip():
                # нашли канон — не нужно расширяться выше
                break
        # Возможно продолжение под значением (вплоть до 3 строк, пропуская reference-comment)
        for k in range(1, 4):
            if i+k >= len(lines): break
            nxt = lines[i+k].rstrip()
            ns = nxt.strip()
            if not ns: break
            # пропустить reference-comment строки
            if re.match(r"^\s{30,}[<>]?\s*\d", nxt):
                continue
            if not re.match(r"^[А-ЯЁа-яёA-Za-z]", ns): break
            cut = re.split(r"\s{4,}", ns, maxsplit=1)[0].strip()
            digits_n = sum(1 for c in cut if c.isdigit())
            if not (cut and re.search(r"[А-ЯЁA-Za-zа-яё]", cut) and digits_n <= len(cut) // 2):
                break
            name_parts.append(cut)
            if normalize_key(" ".join(name_parts)) != " ".join(name_parts).strip():
                break
        if not name_parts:
            continue
        name = " ".join(name_parts).strip()
        val = m.group("val")
        unit = m.group("unit").strip()
        ref = (m.group("ref") or "").strip()
        v = re.match(r"^[<>≥≤]?\s*(\d+([.,]\d+)?)$", val)
        if not v: continue
        _add_param(results, seen, name, val, unit, ref)

    # Третий проход: расслабленный DNKOM_NOREF — ловит line-format когда после value+unit идёт текст ref'а.
    for m in RE_DNKOM_NOREF.finditer(text):
        name = m.group(1).strip()
        val = m.group("val").strip()
        unit = (m.groupdict().get("unit") or "").strip()
        v = re.match(r"^[<>≥≤]?\s*(\d+([.,]\d+)?)$", val)
        if not v: continue
        if unit in {"-", ",", "—", "–"} or len(unit) < 1: continue
        # Попробуем извлечь референс — ищем после value+unit на той же строке: «<X», «X-Y», «>X»
        ref = ""
        tail = text[m.end():m.end()+200]
        tail_line = tail.split("\n", 1)[0]
        rm = re.search(r"\s+(?P<r>(?:<\s*" + NUMBER + r"|>\s*" + NUMBER + r"|" + NUMBER + r"\s*[-–—]\s*" + NUMBER + r"))", tail_line)
        if rm: ref = rm.group("r").strip()
        _add_param(results, seen, name, val, unit, ref)

    # NIARMEDIC-style: unit перед value. Применяем только если файл/чанк выглядит как NIARMEDIC.
    is_niarmedic = "ниармедик" in text.lower() or "архимед" in text.lower() or "клиника новых медицинских технологий" in text.lower()
    if is_niarmedic:
        for m in RE_NIARMEDIC.finditer(text):
            name = m.group(1).strip()
            val = m.group("val").strip()
            unit = m.group("unit").strip()
            if is_garbage_name(name):
                continue
            v = re.match(r"^[<>≥≤]?\s*(\d+([.,]\d+)?)$", val)
            if not v:
                continue
            # unit не должен содержать цифр
            if re.search(r"\d", unit):
                continue
            # для NIARMEDIC попробуем поднять имя на 1 строку выше («Антитела IgG к коронавирусу»)
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_idx_text = text[:line_start]
            prev_lines = line_idx_text.rstrip("\n").split("\n")
            for k in range(len(prev_lines)-1, max(-1, len(prev_lines)-3), -1):
                p = prev_lines[k].strip()
                if not p: continue
                if re.search(r"\d", p): break
                if not re.match(r"^[А-ЯЁA-Za-z]", p): break
                name = p + " " + name
                break
            canon = normalize_key(name)
            key = (canon, val.replace(" ", ""))
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "raw_name": name, "name": canon,
                "value": val.replace(",", "."), "unit": unit, "ref": "",
            })

    # Спец-кейс: Анализ CAG-повторов гена AR (генетика, DNKOM)
    if "CAG-повторов" in text or "CAG повторов" in text or "(CAG)n repeat" in text:
        # Ищем число CAG-повторов в тексте заключения: «(20; N = 18-22)»
        mm = re.search(r"\((\d{1,3})\s*;\s*N\s*=\s*(\d{1,3})\s*[-–—]\s*(\d{1,3})\)", text)
        if mm:
            v = mm.group(1)
            ref = f"{mm.group(2)}-{mm.group(3)}"
            results.append({
                "raw_name": "AR: (CAG)n repeat",
                "name": "CAG-повторы (ген AR)",
                "value": v,
                "unit": "повт.",
                "ref": ref,
            })
            seen.add(("CAG-повторы (ген AR)", v))

    # Качественные тесты
    for m in RE_QUAL.finditer(text):
        name = m.group(1).strip()
        result = m.group(2).strip()
        if is_garbage_name(name):
            continue
        canon = normalize_key(name)
        key = (canon, result.lower())
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "raw_name": name,
            "name": canon,
            "value": result,
            "unit": "",
            "ref": "",
        })
    return results


# ── Копрограмма (качественная): заносим только ОТКЛОНЕНИЯ от нормы + pH ──
# Ключ — нормализованное имя поля из бланка; значение — отображаемое имя маркера
# (суффикс «(кал)» разводит с кровяными Эритроциты/Лейкоциты и т.п.).
COPRO_MAP = {
    "консистенцияформа": "Консистенция кала", "консистенция": "Консистенция кала",
    "цвет": "Цвет кала", "слизь": "Слизь (кал)", "стеркобилин": "Стеркобилин (кал)",
    "билирубин": "Билирубин (кал)", "нейтральныйжир": "Нейтральный жир (кал)",
    "жирныекислоты": "Жирные кислоты (кал)", "мыла": "Мыла (кал)",
    "крахмалвнутриклеточный": "Крахмал внутрикл. (кал)",
    "крахмалвнеклеточный": "Крахмал внекл. (кал)",
    "мышечныеволокнасисчерченностью": "Мыш. волокна исчерч. (кал)",
    "мышечныеволокнабезисчерченности": "Мыш. волокна неисчерч. (кал)",
    "соединительнаяткань": "Соединительная ткань (кал)",
    "флорайодофильная": "Флора йодофильная (кал)",
    "растительнаяклетчатканепереваримая": "Клетчатка непереваримая (кал)",
    "растительнаяклетчаткапереваримая": "Клетчатка переваримая (кал)",
    "эритроциты": "Эритроциты (кал)", "лейкоциты": "Лейкоциты (кал)",
    "яйцагельминтов": "Яйца гельминтов (кал)", "простейшие": "Простейшие (кал)",
    "спорыгриба": "Споры гриба (кал)", "мицелийгриба": "Мицелий гриба (кал)",
}


def _copro_key(x: str) -> str:
    return re.sub(r"[\s\.,]+", "", x.lower()).replace("ё", "е")


def _copro_eq(a: str, b: str) -> bool:
    n = lambda x: re.sub(r"\s+", " ", x.lower().replace("ё", "е")).strip(" .")
    return n(a) == n(b)


def parse_coprogram(text: str) -> list[dict]:
    """Копрограмма → только отклонения (результат ≠ норма) + pH. group='coprogram'."""
    if "КОПРОГРАММА" not in text.upper():
        return []
    out = []
    for ln in text.split("\n"):
        s = ln.strip()
        if not s or "исследование" in s.lower() or "показатель" in s.lower():
            continue
        parts = re.split(r"\s{2,}", s)
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        result = parts[1].strip()
        ref = parts[2].strip() if len(parts) >= 3 else ""
        key = _copro_key(name)
        if key in ("ph", "рн") and re.match(r"^\d", result):
            out.append({"name": "Кислотность кала", "raw_name": name,
                        "value": result.replace(",", "."), "unit": "", "ref": ref,
                        "group": "coprogram"})
            continue
        disp = COPRO_MAP.get(key)
        if not disp or not result:
            continue
        if ref and _copro_eq(result, ref):
            continue  # норма — пропускаем
        out.append({"name": disp, "raw_name": name, "value": result, "unit": "",
                    "ref": ref, "group": "coprogram"})
    if "оксалат" in text.lower():
        out.append({"name": "Оксалаты (кал)", "raw_name": "оксалаты", "value": "обнаружены",
                    "unit": "", "ref": "не обнаружены", "group": "coprogram"})
    return out


def split_visits(text: str) -> list[tuple[str, str]]:
    """Если в одном файле несколько DNKOM-визитов с разными датами,
    возвращает список (date, chunk_text). Иначе [(None, text)]."""
    # Якорь начала визита — строка «Ф.И.О.: <любое имя>» (обезличено).
    anchors = list(re.finditer(r"^[\s ]*Ф\.И\.О\.:\s*\S", text, re.MULTILINE | re.IGNORECASE))
    if len(anchors) < 2:
        return [(None, text)]
    # Разбиваем по якорям
    chunks = []
    for i, m in enumerate(anchors):
        start = m.start()
        end = anchors[i+1].start() if i+1 < len(anchors) else len(text)
        chunk = text[start:end]
        # Извлечём дату из чанка
        d = None
        for pat in [r"Взятие биоматериала:\s*(\d{2})\.(\d{2})\.(\d{4})",
                    r"Регистрация биоматериала:\s*(\d{2})\.(\d{2})\.(\d{4})"]:
            mm = re.search(pat, chunk)
            if mm:
                d = f"{mm.group(3)}-{mm.group(2)}-{mm.group(1)}"
                break
        chunks.append((d, chunk))
    # Если все даты одинаковые — вернём один большой чанк
    dates = {d for d, _ in chunks if d}
    if len(dates) <= 1:
        return [(next(iter(dates), None), text)]
    return chunks


def main():
    # Импортируем спец-парсеры (спермограмма, TUNEL, биоимпеданс)
    try:
        from parse_special import parse_special
    except ImportError:
        parse_special = None

    out = []
    for txt in sorted(TXT_DIR.glob("*.txt")):
        text = txt.read_text(encoding="utf-8", errors="ignore")
        lab = detect_lab(txt.stem, text)
        atype = detect_type(txt.stem, text)

        # Разбиение по визитам, если в файле несколько дат
        visits = split_visits(text)
        is_saliva = "слюн" in txt.stem.lower() or "слюн" in text.lower()[:500]
        for visit_date_chunk, chunk_text in visits:
            date = visit_date_chunk or extract_date(txt.stem, text)
            params = parse_text(chunk_text)

            # Контекстная корректировка: Тестостерон в слюне.
            # Признаки: (а) биоматериал «слюна»; (б) норма «> X» где X<5;
            # (в) value < 3 нмоль/л (для мужчины 43 лет в крови это резко низко, скорее слюна).
            for p in params:
                if p["name"] != "Тестостерон общий": continue
                low_ref = re.match(r"\s*>\s*(\d+(?:[.,]\d+)?)\s*$", p.get("ref", ""))
                ref_low_val = float(low_ref.group(1).replace(",", ".")) if low_ref else None
                try: val_num = float(p["value"].replace(",", "."))
                except: val_num = None
                if (is_saliva
                    or (ref_low_val is not None and ref_low_val < 5)
                    or (val_num is not None and val_num < 3 and ("нмоль" in p.get("unit","") or "nmol" in p.get("unit","")))):
                    p["name"] = "Тестостерон в слюне"

            # Спец-парсер: спермограмма / TUNEL / биоимпеданс. Если он что-то нашёл —
            # его записи приоритетнее: убираем из общего парсера записи с теми же именами.
            if parse_special:
                sp_records = parse_special(txt.stem, chunk_text)
                sp_names = {sp["name"] for sp in sp_records}
                if sp_names:
                    params = [p for p in params if p["name"] not in sp_names]
                for sp in sp_records:
                    params.append({
                        "raw_name": sp["name"],
                        "name": sp["name"],
                        "value": sp["value"],
                        "unit": sp["unit"],
                        "ref": sp["ref"],
                        "group": sp.get("group", ""),
                    })

            out.append({
                "file": txt.stem,
                "date": date,
                "lab": lab,
                "type": atype,
                "params_count": len(params),
                "params": params,
            })
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    # сводка
    total = sum(r["params_count"] for r in out)
    print(f"Файлов: {len(out)}")
    print(f"Параметров: {total}")
    no_date = [r["file"] for r in out if not r["date"]]
    no_params = [r["file"] for r in out if r["params_count"] == 0]
    print(f"Без даты: {len(no_date)}: {no_date}")
    print(f"Без параметров: {len(no_params)}")
    for f in no_params[:30]:
        print(" -", f)


if __name__ == "__main__":
    main()
