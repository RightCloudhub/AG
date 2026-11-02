# 基于已导入语料的十跳推理题

依据当前已导入的 238 篇原始语料设计。逐文件 SHA-256 与导入清单一致；
以下 31 条引文均逐字匹配原文件，引用位置为从 1 开始的文件行号。

## 计跳与验收

一跳是从当前实体沿一个明确关系到达下一个实体或属性值。取文档、改写句子、
重复确认以及查询次数不计跳；三条主链均无重复节点。子公司关系可依据
“X is a subsidiary of Y”从 Y 反向查找 X，同样只计一跳。

**这是指定关系顺序的证据链测试：必须提交完整的中间节点和引文，单独猜对最终城市不算通过。**
10 / 10 / 11 指满足题面关系组合的链长，不声称它们是全图任意关系下的最短路径。
三题选取的连接在当前原文中均可得到单一候选，且避开了样例与试点语料存在冲突的 CEO 事实。
本次校验的是原文和题目证据链，尚未将应用或 LLM 的回答作为通过记录。

## Q1：安全产品到硬件企业（10 跳）

**可直接使用的题目：**

从 Nimbus Shield 出发，找到其生产商的现任 CEO 曾供职的公司；再找到这家公司生产的产品、该产品的供应商，以及供应商的竞争对手。接着依次追踪这家竞争对手的子公司、该子公司的竞争对手、后者的子公司。最后一家公司的总部在哪座城市？请按上述关系顺序列出全部中间实体，并为每一跳提供原文证据。

**参考答案：Portland。**

| 跳数 | 当前实体 → 关系 → 下一实体 | 原文依据 | 来源 |
| --- | --- | --- | --- |
| 1 | Nimbus Shield → 生产商 → **Nimbus Security** | “Nimbus Shield is a security suite product produced by Nimbus Security.” | [30_product_nimbus_shield.md:L3](../../data/pilot/raw/30_product_nimbus_shield.md#L3) |
| 2 | Nimbus Security → 现任 CEO → **Yael Cohen** | “The current CEO of Nimbus Security is Yael Cohen.” | [11_company_nimbus_security.md:L5](../../data/pilot/raw/11_company_nimbus_security.md#L5) |
| 3 | Yael Cohen → 前雇主 → **Lattice AI** | “Before Nimbus Security, Yael Cohen worked at Lattice AI.” | [20_person_yael_cohen.md:L5](../../data/pilot/raw/20_person_yael_cohen.md#L5) |
| 4 | Lattice AI → 生产的产品 → **Lattice Train Hub** | “Lattice AI produces Lattice Train Hub.” | [11_company_lattice_ai.md:L9](../../data/pilot/raw/11_company_lattice_ai.md#L9) |
| 5 | Lattice Train Hub → 供应商 → **Polaris Cloud** | “Polaris Cloud supplies components or services for Lattice Train Hub.” | [30_product_lattice_train_hub.md:L5](../../data/pilot/raw/30_product_lattice_train_hub.md#L5) |
| 6 | Polaris Cloud → 竞争对手 → **NovaTech Industries** | “Polaris Cloud competes with NovaTech Industries in the cloud infrastructure market.” | [11_company_polaris_cloud.md:L11](../../data/pilot/raw/11_company_polaris_cloud.md#L11) |
| 7 | NovaTech Industries → 子公司 → **Nova Edge Labs** | “Nova Edge Labs is a subsidiary of NovaTech Industries.” | [11_company_nova_edge_labs.md:L5](../../data/pilot/raw/11_company_nova_edge_labs.md#L5) |
| 8 | Nova Edge Labs → 竞争对手 → **Helix Compute** | “Nova Edge Labs competes with Helix Compute in the edge computing R&D market.” | [11_company_nova_edge_labs.md:L11](../../data/pilot/raw/11_company_nova_edge_labs.md#L11) |
| 9 | Helix Compute → 子公司 → **Jasper Boards** | “Jasper Boards is a subsidiary of Helix Compute.” | [11_company_jasper_boards.md:L5](../../data/pilot/raw/11_company_jasper_boards.md#L5) |
| 10 | Jasper Boards → 总部城市 → **Portland** | “Jasper Boards is a company in the hardware boards sector, headquartered in Portland.” | [11_company_jasper_boards.md:L3](../../data/pilot/raw/11_company_jasper_boards.md#L3) |

## Q2：零售平台到气候数据企业（10 跳）

**可直接使用的题目：**

以 Drift Storefront 为起点，找到其生产商的现任 CEO 的前雇主，再找到这家前雇主的母公司。继续追踪母公司的现任 CEO 及其前雇主，找到后一家公司生产的产品和该产品的供应商，最后找到供应商的子公司。这个子公司的总部在哪座城市？请按上述关系顺序列出全部中间实体，并为每一跳提供原文证据。

**参考答案：Reykjavik。**

| 跳数 | 当前实体 → 关系 → 下一实体 | 原文依据 | 来源 |
| --- | --- | --- | --- |
| 1 | Drift Storefront → 生产商 → **Drift Commerce** | “Drift Storefront is a e-commerce product produced by Drift Commerce.” | [30_product_drift_storefront.md:L3](../../data/pilot/raw/30_product_drift_storefront.md#L3) |
| 2 | Drift Commerce → 现任 CEO → **Jordan Lee** | “The current CEO of Drift Commerce is Jordan Lee.” | [11_company_drift_commerce.md:L5](../../data/pilot/raw/11_company_drift_commerce.md#L5) |
| 3 | Jordan Lee → 前雇主 → **Mosaic Retail** | “Before Drift Commerce, Jordan Lee worked at Mosaic Retail.” | [20_person_jordan_lee.md:L5](../../data/pilot/raw/20_person_jordan_lee.md#L5) |
| 4 | Mosaic Retail → 母公司 → **Northwind Analytics** | “Mosaic Retail is a subsidiary of Northwind Analytics.” | [11_company_mosaic_retail.md:L5](../../data/pilot/raw/11_company_mosaic_retail.md#L5) |
| 5 | Northwind Analytics → 现任 CEO → **Nora Lindqvist** | “The current CEO of Northwind Analytics is Nora Lindqvist.” | [11_company_northwind_analytics.md:L7](../../data/pilot/raw/11_company_northwind_analytics.md#L7) |
| 6 | Nora Lindqvist → 前雇主 → **Frost Data** | “Before Northwind Analytics, Nora Lindqvist worked at Frost Data.” | [20_person_nora_lindqvist.md:L5](../../data/pilot/raw/20_person_nora_lindqvist.md#L5) |
| 7 | Frost Data → 生产的产品 → **Frost Lakehouse** | “Frost Data produces Frost Lakehouse.” | [11_company_frost_data.md:L9](../../data/pilot/raw/11_company_frost_data.md#L9) |
| 8 | Frost Lakehouse → 供应商 → **Yellowstone Data** | “Yellowstone Data supplies components or services for Frost Lakehouse.” | [30_product_frost_lakehouse.md:L5](../../data/pilot/raw/30_product_frost_lakehouse.md#L5) |
| 9 | Yellowstone Data → 子公司 → **Glacier Analytics** | “Glacier Analytics is a subsidiary of Yellowstone Data.” | [11_company_glacier_analytics.md:L5](../../data/pilot/raw/11_company_glacier_analytics.md#L5) |
| 10 | Glacier Analytics → 总部城市 → **Reykjavik** | “Glacier Analytics is a company in the climate data sector, headquartered in Reykjavik.” | [11_company_glacier_analytics.md:L3](../../data/pilot/raw/11_company_glacier_analytics.md#L3) |

## Q3：农业软件到通信企业（11 跳）

**可直接使用的题目：**

从 Terra Yield OS 的生产商出发，找到其竞争对手生产的产品及该产品的供应商。继续追踪供应商的现任 CEO 的前雇主，找到这家前雇主的竞争对手及该竞争对手的子公司。最后，找到这个子公司的竞争对手的母公司。该母公司的总部在哪座城市？请按上述关系顺序列出全部中间实体，并为每一跳提供原文证据。

**参考答案：Stockholm。**

| 跳数 | 当前实体 → 关系 → 下一实体 | 原文依据 | 来源 |
| --- | --- | --- | --- |
| 1 | Terra Yield OS → 生产商 → **Terra Farm Tech** | “Terra Yield OS is a agri OS product produced by Terra Farm Tech.” | [30_product_terra_yield_os.md:L3](../../data/pilot/raw/30_product_terra_yield_os.md#L3) |
| 2 | Terra Farm Tech → 竞争对手 → **Kinetic Sensors** | “Terra Farm Tech competes with Kinetic Sensors in the agritech market.” | [11_company_terra_farm_tech.md:L7](../../data/pilot/raw/11_company_terra_farm_tech.md#L7) |
| 3 | Kinetic Sensors → 生产的产品 → **Kinetic Node** | “Kinetic Sensors produces Kinetic Node.” | [11_company_kinetic_sensors.md:L9](../../data/pilot/raw/11_company_kinetic_sensors.md#L9) |
| 4 | Kinetic Node → 供应商 → **Summit Robotics** | “Summit Robotics supplies components or services for Kinetic Node.” | [30_product_kinetic_node.md:L5](../../data/pilot/raw/30_product_kinetic_node.md#L5) |
| 5 | Summit Robotics → 现任 CEO → **Lina Schneider** | “The current CEO of Summit Robotics is Lina Schneider.” | [11_company_summit_robotics.md:L5](../../data/pilot/raw/11_company_summit_robotics.md#L5) |
| 6 | Lina Schneider → 前雇主 → **Kite Drones** | “Before Summit Robotics, Lina Schneider worked at Kite Drones.” | [20_person_lina_schneider.md:L5](../../data/pilot/raw/20_person_lina_schneider.md#L5) |
| 7 | Kite Drones → 竞争对手 → **Zenith Aerospace** | “Kite Drones competes with Zenith Aerospace in the commercial drones market.” | [11_company_kite_drones.md:L9](../../data/pilot/raw/11_company_kite_drones.md#L9) |
| 8 | Zenith Aerospace → 子公司 → **Orbit Satcom** | “Orbit Satcom is a subsidiary of Zenith Aerospace.” | [11_company_orbit_satcom.md:L5](../../data/pilot/raw/11_company_orbit_satcom.md#L5) |
| 9 | Orbit Satcom → 竞争对手 → **Willow Telecom** | “Orbit Satcom competes with Willow Telecom in the satellite comms market.” | [11_company_orbit_satcom.md:L9](../../data/pilot/raw/11_company_orbit_satcom.md#L9) |
| 10 | Willow Telecom → 母公司 → **Lumen Networks** | “Willow Telecom is a subsidiary of Lumen Networks.” | [11_company_willow_telecom.md:L5](../../data/pilot/raw/11_company_willow_telecom.md#L5) |
| 11 | Lumen Networks → 总部城市 → **Stockholm** | “Lumen Networks is a company in the networking sector, headquartered in Stockholm.” | [11_company_lumen_networks.md:L3](../../data/pilot/raw/11_company_lumen_networks.md#L3) |

## 使用方式

提问时复制题目段落，保留“全部中间实体与每跳证据”的要求；参考答案和表格留作人工评分。
验收时逐项检查实体、关系方向、原文是否支持该关系以及最终城市。不得把公司母子关系
推断成供应关系，也不得从人物与公司的同段共现推断任职或前雇主。

引擎的 `max_hops` 是执行预算，和这里的原文关系边数不是一一对应；
若实际回答只给最终城市或省略中间人物 / 产品，应按证据链不完整处理。
