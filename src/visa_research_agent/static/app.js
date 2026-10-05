const form = document.querySelector("#plan-form");
const destinationSelect = document.querySelector("#destination");
const nationalitySelect = document.querySelector("#nationality");
const residenceSelect = document.querySelector("#residence");
const regionSelect = document.querySelector("#region");
const regionGroup = document.querySelector("#region-group");
const purposeSelect = document.querySelector("#purpose");
const generateButton = document.querySelector("#generate-button");
const progress = document.querySelector("#progress");
const progressSteps = document.querySelector("#progress-steps");
const progressElapsed = document.querySelector("#progress-elapsed");
const errorMessage = document.querySelector("#error-message");
const results = document.querySelector("#results");
const passportNote = document.querySelector("#passport-note");
const passportChoices = document.querySelector("#passport-choices");

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function externalLink(label, url, className = "") {
  const link = element("a", className, label);
  link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer noopener";
  return link;
}

function panel(title, eyebrow, variant = "") {
  const container = element("section", variant ? `panel panel--${variant}` : "panel");
  const header = element("div", "panel-header");
  const headingGroup = element("div", "panel-heading");
  headingGroup.append(element("p", "eyebrow", eyebrow), element("h2", "", title));
  header.append(headingGroup);
  container.append(header);
  return { container, header };
}

function sourceSubtext(source) {
  const retrieved = new Date(source.retrieved_at).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  const freshness = source.is_stale ? "could not be re-checked since" : "retrieved";
  return `${source.authority} · ${freshness} ${retrieved}`;
}

// One card component for every outgoing link. Role is either the single action
// the traveller must take ("action") or supporting provenance ("evidence").
function linkCard(role, title, url, subtext, isStale = false) {
  const card = externalLink("", url, `link-card link-card--${role}${isStale ? " link-card--stale" : ""}`);
  const body = element("div", "link-card-body");
  const roleRow = element("span", "link-card-role-row");
  roleRow.append(element("span", "link-card-role", role === "action" ? "Go here" : "Evidence"));
  if (isStale) roleRow.append(element("span", "stale-badge", "Not re-checked"));
  body.append(roleRow, element("span", "link-card-title", title));
  if (subtext) body.append(element("span", "link-card-sub", subtext));
  card.append(body, element("span", "link-card-arrow", "↗"));
  return card;
}

// Give every source a single home section so no link is repeated across the page.
// Priority runs most-specific first; the first section to claim a source keeps it.
function assignSourceHomes(plan) {
  const home = new Map();
  const claim = (ids, section) =>
    (ids || []).forEach((id) => {
      if (!home.has(id)) home.set(id, section);
    });
  claim(plan.application_document_source_ids, "requirements");
  claim(plan.decision_source_ids, "decision");
  if (plan.where_to_apply) claim(plan.where_to_apply.source_ids, "apply");
  plan.application_steps.forEach((step) => claim(step.source_ids, "timeline"));
  return home;
}

function renderEvidence(sourceIds, ctx, section) {
  const group = element("div", "link-cards");
  [...new Set(sourceIds)].forEach((id) => {
    if (ctx.home.get(id) !== section || ctx.seen.has(id)) return;
    const source = ctx.sourceMap.get(id);
    if (!source || ctx.seenUrls.has(String(source.url))) return;
    ctx.seen.add(id);
    ctx.seenUrls.add(String(source.url));
    group.append(
      linkCard("evidence", source.title, source.url, sourceSubtext(source), source.is_stale),
    );
  });
  return group;
}

function appendIfFilled(container, group) {
  if (group.childElementCount) container.append(group);
}

function appendLinks(item, failures) {
  failures.forEach((failure, index) => {
    if (index) item.append(document.createTextNode(", "));
    item.append(externalLink(failure.attempted_url, failure.attempted_url));
  });
}

// One authority's refusals as one sentence. The pages judged able to hold the visa decision lead,
// said as "may" because nobody read them; the rest follow, linked, never dropped (entry 32 bounds
// what may resolve a corridor, never what is reported).
function refusalItem(authority, failures) {
  const decision = failures.filter((failure) => failure.may_hold_decision);
  const others = failures.filter((failure) => !failure.may_hold_decision);
  const item = element(
    "li",
    "",
    `${authority} does not permit automated retrieval, so its guidance could not be verified here.`,
  );
  if (decision.length) {
    item.append(document.createTextNode(
      decision.length === 1
        ? " Start with the page that may say whether you need a visa: "
        : " Start with the pages that may say whether you need a visa: ",
    ));
    appendLinks(item, decision);
    item.append(document.createTextNode(decision.length === 1 ? " — open it yourself to check." : " — open them yourself to check."));
    if (others.length) {
      item.append(document.createTextNode(others.length === 1 ? " It also refused " : " Other pages it refused: "));
      appendLinks(item, others);
      item.append(document.createTextNode("."));
    }
    return item;
  }
  item.append(document.createTextNode(others.length === 1 ? " It is published at " : " The refused pages are at "));
  appendLinks(item, others);
  item.append(document.createTextNode(others.length === 1 ? " — open it yourself to check." : " — open them yourself to check."));
  return item;
}

// The pages behind a partial plan that a traveller can do something about: ones we could not read,
// each linked, and ones we could not re-check. Shown only when there is one to name — a heading over
// an empty list, with a sentence that fits every plan, told the traveller nothing (entry 254).
function renderEvidenceBanner(plan) {
  const staleSources = plan.sources.filter((source) => source.is_stale);
  const missing = plan.unavailable_sources || [];
  if (!missing.length && !staleSources.length) return null;

  const banner = element("div", "evidence-banner");
  banner.append(element("p", "evidence-banner-title", "Pages we could not read or re-check"));

  const list = element("ul");
  // An authority refusing this program is the one gap a traveller can close themselves, so it gets
  // the sentence that says so and links they can open — once per authority. One US plan said it
  // nine times, the fee table beside the visitor-visa page (TODO item 54). Every page keeps its link.
  const refusals = new Map();
  missing.forEach((failure) => {
    if (failure.outcome !== "blocked" || !failure.attempted_url) return;
    if (!refusals.has(failure.authority)) refusals.set(failure.authority, []);
    refusals.get(failure.authority).push(failure);
  });
  refusals.forEach((failures, authority) => list.append(refusalItem(authority, failures)));
  missing.forEach((failure) => {
    if (failure.outcome === "blocked" && failure.attempted_url) return;
    const item = element("li", "", `${failure.title} (${failure.authority}) — ${failure.detail}`);
    // An official page we could not read is still one the traveller can open, so it gets its link.
    if (failure.attempted_url) {
      item.append(document.createTextNode(". It is at "));
      item.append(externalLink(failure.attempted_url, failure.attempted_url));
      item.append(document.createTextNode(" — open it yourself to check."));
    }
    list.append(item);
  });
  staleSources.forEach((source) => {
    const checked = new Date(source.retrieved_at).toLocaleDateString();
    list.append(
      element(
        "li",
        "",
        `${source.title} could not be re-checked; showing the copy retrieved ${checked}.`,
      ),
    );
  });
  banner.append(list);
  return banner;
}

// A question an authority answers only through its own questionnaire is guidance it has published,
// not a gap: the traveller can get the real answer in a few clicks, which is more than a page we
// could not find gives them. So a tool is offered beside the question it settles rather than filed
// under caveats — and never as evidence, because nobody has answered it.
const TOOL_TOPICS = {
  visa_decision: ["decides whether you need a visa", "the decision for your own passport and trip"],
  document_checklist: ["lists the documents through a questionnaire", "the document list for your own application"],
  application_route: ["sets out where to apply through a questionnaire", "where and how you apply"],
  fees: ["works out the fee through a questionnaire", "the fee for your own application"],
  processing_times: ["works out the processing time through a questionnaire", "how long your own application should take"],
  general_entry: ["sets out entry requirements through a questionnaire", "the entry requirements for your own trip"],
};

function toolsFor(plan, topic) {
  return (plan.official_tools || []).filter((tool) => tool.topic === topic);
}

function toolCallout(tool) {
  const [what, gives] = TOOL_TOPICS[tool.topic] || ["answers this through a questionnaire", "the answer for your own trip"];
  const callout = element("div", "decision-tool");
  callout.append(element("p", "decision-tool-title", `${tool.authority} ${what}`));
  const body = element("p", "", `This is the official tool, and answering its questions gives you ${gives}. It asks things we cannot answer on your behalf. Open it at `);
  body.append(externalLink(tool.url, tool.url));
  body.append(document.createTextNode("."));
  callout.append(body);
  return callout;
}

const DELEGATE_TOPICS = {
  visa_decision: "whether you need a visa",
  document_checklist: "the documents you will need",
  application_route: "how and where to apply",
  fees: "what it costs",
  processing_times: "how long it takes",
  general_entry: "the entry requirements for your trip",
};

function delegatesFor(plan, topic) {
  return (plan.delegated_services || []).filter((service) => service.topic === topic);
}

function delegateCallout(service) {
  const gives = DELEGATE_TOPICS[service.topic] || "the answer for your own trip";
  const callout = element("div", "decision-tool decision-tool--delegated");
  callout.append(element("p", "decision-tool-title", `Published by ${service.provider}, a company rather than a government site`));
  // What was seen is a government page linking to the company for this topic. Whether the authority
  // also publishes it somewhere of its own is not something anyone here established (TODO item 9).
  const body = element("p", "", `For ${gives}, the authority's own page at `);
  body.append(externalLink(service.appointed_by, service.appointed_by));
  body.append(document.createTextNode(" sends you to "));
  body.append(externalLink(service.url, service.url));
  // The honest limit, said where the link is rather than in a footnote. This is a commercial site,
  // so nothing in this plan was read from it and nothing here can vouch for what it says.
  body.append(document.createTextNode(", a company it contracts with. We have not read that site and nothing in this plan comes from it \u2014 check it against the government page above."));
  callout.append(body);
  return callout;
}

function appendDelegates(container, plan, topic, seen) {
  delegatesFor(plan, topic).forEach((service) => {
    if (seen) {
      if (seen.has(service.url)) return;
      seen.add(service.url);
    }
    container.append(delegateCallout(service));
  });
}

function appendTools(container, plan, topic, seen) {
  toolsFor(plan, topic).forEach((tool) => {
    // One page can settle several questions, and each is a different answer worth giving — France's
    // mission page covers the decision, the documents and the fee. So a repeated URL is fine across
    // panels, where the panel supplies the question. It is not fine inside the catch-all panel that
    // carries fees, times and entry conditions together, which would just show the same link thrice.
    if (seen) {
      if (seen.has(tool.url)) return;
      seen.add(tool.url);
    }
    container.append(toolCallout(tool));
  });
}

// A plan whose decision is a stated "no" describes an entry, not an application: no route, no
// checklist, and steps that are duties on arrival rather than stages of a submission. Three panels
// below would otherwise describe an application that does not exist — see DECISIONS entry 95.
// Keyed on false rather than "not true" on purpose: an unverified decision is null, and null keeps
// the application shape so the four questions stay visible for the traveller to notice.
// The visa type is the source's own words (rule 8j), often lowercase mid-sentence on the page.
function sentenceCase(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function needsNoVisa(plan) {
  return plan.visa_required === false;
}

// Why a plan is partial, in the words of the one reason that applies, first match wins. A null
// decision has no label here: its decision chip already says "Uncertain".
function partialReason(plan) {
  if (plan.decision_condition) return "Depends on your trip";
  if (plan.visa_required === null) return null;
  if ((plan.unavailable_sources || []).length) return "Some pages unreadable";
  if (plan.sources.some((source) => source.is_stale)) return "Some pages not re-checked";
  if (!needsNoVisa(plan) && !(plan.application_document_source_ids || []).length) {
    return "No checklist confirmed";
  }
  return null;
}

function renderDecision(plan, ctx) {
  const { container, header } = panel(plan.destination, "Visa decision");
  const [decision, tone] =
    plan.visa_required === null
      ? ["Uncertain", "uncertain"]
      : plan.visa_required
        ? ["Visa required", "visa"]
        : ["No visa required", "no-visa"];
  const chips = element("div", "chip-group");
  chips.append(element("span", `decision-chip decision-chip--${tone}`, decision));
  // A partial plan says which way it is partial, never only that it is (entry 254).
  const status = plan.status === "verified" ? "Evidence verified" : partialReason(plan);
  if (status) chips.append(element("span", `status-chip status-chip--${plan.status}`, status));
  header.append(chips);
  // A decision that holds only on a fact about the trip the traveller has not confirmed — the
  // layover, the length of stay — says so beside the answer, never only in the explanation below
  // it: "No visa required" read alone would be wrong for the other side of it (entry 250).
  if (plan.decision_condition) {
    container.append(
      element("p", "decision-condition", `Only if ${plan.decision_condition.replace(/\.$/, "")}.`),
    );
  }
  // The visa type leads where the plan names one. "Visa type unresolved" in front of an explanation
  // that already says why only stacked a third hedge on a conditional answer (entry 254).
  const lead =
    needsNoVisa(plan) || !plan.visa_type
      ? plan.explanation
      : `${sentenceCase(plan.visa_type)}. ${plan.explanation}`;
  container.append(element("p", "lead", lead));
  appendTools(container, plan, "visa_decision");
  appendDelegates(container, plan, "visa_decision");
  appendIfFilled(container, renderEvidence(plan.decision_source_ids, ctx, "decision"));
  return container;
}

// Pages about this trip's visa that this run met and could not read, named so the traveller can
// open them, never described, because nobody read them (entry 219). Australia's Tourist stream page
// is where its Apply button is, and its site refuses our browser.
function appendUnreadVisaPages(container, plan) {
  const pages = (plan.unavailable_sources || []).filter(
    (failure) => String(failure.source_id).startsWith("visa_page_unread_") && failure.attempted_url,
  );
  if (!pages.length) return;
  container.append(
    element(
      "p",
      "lead",
      pages.length === 1
        ? "The official page below looks like the one for your visa, but we could not read it, so open it yourself."
        : "The official pages below look like the ones for your visa, but we could not read them, so open them yourself.",
    ),
  );
  const group = element("div", "link-cards");
  pages.forEach((failure) => {
    group.append(
      linkCard(
        "action",
        failure.title,
        failure.attempted_url,
        `${failure.authority} \u00b7 not read here: ${failure.detail}`,
      ),
    );
  });
  container.append(group);
}

// Whether the traveller must travel to the post (TODO item 71, DECISIONS entry 252). Set only where
// a cited page says so; anything else is "not stated", never "no".
const IN_PERSON = {
  required: "Yes \u2014 you must attend in person",
  not_required: "No \u2014 the pages say no visit is needed",
  unstated: "Not stated on the pages read",
};

function renderApplicationLocation(plan, ctx) {
  const { container } = panel("Where to apply", "Application route");
  const location = plan.where_to_apply;
  if (!location) {
    // "Unresolved" is a claim about a search that failed, and it is false when the traveller needs
    // no visa: there is nowhere to apply because the question does not arise.
    container.append(
      element(
        "p",
        "lead",
        needsNoVisa(plan)
          ? "There is nowhere to apply \u2014 this traveller needs no visa, so there is no application to make."
          : "The application location remains unresolved.",
      ),
    );
    appendUnreadVisaPages(container, plan);
    appendTools(container, plan, "application_route");
    appendDelegates(container, plan, "application_route");
    return container;
  }
  appendUnreadVisaPages(container, plan);
  appendTools(container, plan, "application_route");
  appendDelegates(container, plan, "application_route");

  const grid = element("div", "detail-grid");
  // Only what the pages stated: a location where one is given, and whether to attend where a page
  // says. "Online" under a method that already says online, and "Not stated" under "In person",
  // repeated the cell beside them or said nothing (entry 257).
  const details = [
    ["Authority", location.authority],
    ["Method", location.application_method],
  ];
  if (location.location) details.push(["Location", location.location]);
  if (IN_PERSON[location.in_person] && location.in_person !== "unstated") {
    details.push(["In person", IN_PERSON[location.in_person]]);
  }
  details.forEach(([label, value]) => {
    const cell = element("div", "detail-cell");
    cell.append(element("span", "", label), element("p", "", value));
    grid.append(cell);
  });
  container.append(grid);

  const actions = element("div", "link-cards");
  ctx.seenUrls.add(String(location.application_url));
  actions.append(
    linkCard("action", "Official application route", location.application_url, location.authority),
  );
  container.append(actions);
  appendIfFilled(container, renderEvidence(location.source_ids, ctx, "apply"));
  return container;
}

function renderRequirements(plan, ctx) {
  const checklistTools = toolsFor(plan, "document_checklist");
  // No checklist source means no documents may be listed, so the panel would be a heading and a
  // caveat above nothing. The absence is not hidden by dropping it: it is stated under unresolved
  // questions, which the plan is structurally required to carry when there is no checklist source.
  // Unless the authority publishes its list through a questionnaire — then there is somewhere to
  // send the traveller, and that is worth a panel even with nothing to list.
  // A traveller who needs no visa is owed the panel even with nothing in it, because "no documents"
  // is the answer to the question they came with rather than a gap. The other empty case stays
  // dropped: a heading over nothing states an absence the unresolved questions already carry.
  const checklistSources = (plan.application_document_source_ids || [])
    .map((id) => ctx.sourceMap.get(id))
    .filter(Boolean);
  // Likely checklists this run met and could not read: named so the traveller can open them, never
  // described, because nobody read them (TODO item 9, entry 211).
  const unreadChecklists = (plan.unavailable_sources || []).filter(
    (failure) => String(failure.source_id).startsWith("checklist_unread_") && failure.attempted_url,
  );
  if (
    !plan.requirements.length &&
    !checklistSources.length &&
    !unreadChecklists.length &&
    !checklistTools.length &&
    !needsNoVisa(plan)
  ) {
    return null;
  }

  const { container } = panel("Visa application documents", "Official checklist");

  // The authority's own checklist, linked rather than copied — the owner's decision, entry 211. The
  // traveller reads the list the authority wrote, so nothing we wrote can differ from it.
  if (checklistSources.length && !plan.requirements.length) {
    container.append(
      element(
        "p",
        "lead",
        "The authority publishes the documents for this application in its own checklist. Open it and gather everything it lists for your application.",
      ),
    );
    const group = element("div", "link-cards");
    checklistSources.forEach((source) => {
      ctx.seen.add(source.source_id);
      ctx.seenUrls.add(String(source.url));
      // Titled by what it is, not by the page's own <title>: the German missions' checklist page
      // is called "Welcome", which tells a traveller nothing about what they are opening.
      group.append(
        linkCard(
          "action",
          "Official document checklist",
          source.url,
          `${source.title} \u00b7 ${sourceSubtext(source)}`,
          source.is_stale,
        ),
      );
    });
    container.append(group);
    appendTools(container, plan, "document_checklist");
    appendDelegates(container, plan, "document_checklist");
    return container;
  }

  if (!plan.requirements.length) {
    // Four different reasons end up here and the sentence has to be true of the one that applies.
    // Saying "through its own questionnaire" when the authority actually contracted the work out
    // would misdescribe who published the checklist, which is the thing the reader has to judge.
    // The fourth is not a variant of the other three: nobody withheld this list and nobody
    // contracted it out — there is no application, so there is no list to publish.
    if (needsNoVisa(plan)) {
      container.append(
        element(
          "p",
          "lead",
          "There are no application documents to gather \u2014 this traveller needs no visa. What they must carry and do on arrival is under Before you travel below.",
        ),
      );
      appendTools(container, plan, "document_checklist");
      appendDelegates(container, plan, "document_checklist");
      return container;
    }
    const viaTool = toolsFor(plan, "document_checklist").length > 0;
    const viaDelegate = delegatesFor(plan, "document_checklist").length > 0;
    // Said as what we found, never as what the authority publishes: the same empty list arises when a
    // checklist exists and could not be found or read, and nobody can show that one does not exist.
    let why = "We did not find an official page listing the documents for this application among the pages we could read.";
    if (viaTool && viaDelegate) {
      why += " The authority sets them out through its own questionnaire, and sends applicants to a company it contracts with.";
    } else if (viaTool) {
      why += " The authority sets them out through its own questionnaire.";
    } else if (viaDelegate) {
      why += " The authority sends applicants to a company it contracts with for them.";
    }
    if (unreadChecklists.length) {
      why +=
        unreadChecklists.length === 1
          ? " The page below looks like the official checklist, but we could not read it, so open it yourself."
          : " The pages below look like the official checklist, but we could not read them, so open them yourself.";
    }
    container.append(element("p", "lead", why));
    if (unreadChecklists.length) {
      const group = element("div", "link-cards");
      unreadChecklists.forEach((failure) => {
        group.append(
          linkCard(
            "action",
            failure.title,
            failure.attempted_url,
            `${failure.authority} \u00b7 not read here: ${failure.detail}`,
          ),
        );
      });
      container.append(group);
    }
    appendTools(container, plan, "document_checklist");
    appendDelegates(container, plan, "document_checklist");
    return container;
  }

  container.append(
    element(
      "p",
      "lead",
      "Extracted from the designated official application-document source. Confirm the linked guidance before applying; general entry and travel duties are excluded.",
    ),
  );
  appendTools(container, plan, "document_checklist");
  appendDelegates(container, plan, "document_checklist");
  appendIfFilled(container, renderEvidence(plan.application_document_source_ids, ctx, "requirements"));

  const list = element("div", "requirement-list");
  plan.requirements.forEach((requirement) => {
    const card = element("article", "requirement-card");
    card.append(
      element("h3", "", requirement.name),
      element("p", "", requirement.description),
      element("p", "reason", `Source context: ${requirement.reason_it_applies}`),
    );
    list.append(card);
  });
  container.append(list);
  return container;
}

function renderSteps(plan, ctx) {
  // The steps of a visa-free plan are entry duties, not stages of a submission, and calling them a
  // timeline would describe an application the traveller is not making. Nothing about the list
  // changes; the heading is what has to be true. An entry list may also be empty, where the pages
  // stated the decision and no duty beyond it — the panel is dropped rather than filled.
  const entry = needsNoVisa(plan);
  if (entry && !plan.application_steps.length) return null;
  const { container } = entry
    ? panel("Before you travel", "Entry requirements")
    : panel("Application timeline", "When to do what");
  const list = element("ol", "steps");
  plan.application_steps.forEach((step) => {
    const item = element("li");
    const content = element("div", "step-content");
    content.append(
      element("p", "step-timing", `Timing: ${step.timing}`),
      element("h3", "", step.title),
      element("p", "", step.action),
    );
    // Each step names the pages it rests on, timing included, where the reader meets the claim — a
    // grouped list at the end left a timing with nothing beside it to check it against (entry 257).
    const cited = [...new Set(step.source_ids)].map((id) => ctx.sourceMap.get(id)).filter(Boolean);
    if (cited.length) {
      const from = element("p", "step-sources");
      from.append(document.createTextNode(cited.length === 1 ? "Source: " : "Sources: "));
      cited.forEach((source, index) => {
        if (index) from.append(document.createTextNode(" · "));
        from.append(externalLink(`${source.title} \u2197`, source.url));
      });
      content.append(from);
    }
    item.append(content);
    list.append(item);
  });
  container.append(list);
  return container;
}

// Name the authorities this plan actually rests on, so the caveat is never wrong for a country.
function authoritiesSentence(plan) {
  // One authority is often recorded twice, with and without "www." in its address.
  const byKey = new Map();
  plan.sources.forEach((source) => {
    const key = source.authority.replace(/\(www\./, "(").toLowerCase();
    if (!byKey.has(key)) byKey.set(key, source.authority.replace(/\(www\./, "("));
  });
  const names = [...byKey.values()];
  if (!names.length) return "the responsible authority";
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

// Shown only when it has something particular to say — a page that could not be read or re-checked,
// or an official questionnaire for fees, processing times or entry. Otherwise it was a heading over
// the standing caveat, which now closes the page on its own (entry 257).
function renderReliability(plan) {
  const { container } = panel("Evidence and caveats", "Reliability");
  const before = container.childElementCount;
  const banner = renderEvidenceBanner(plan);
  if (banner) container.append(banner);

  // Fees, processing times and entry conditions have no panel of their own — they live inside the
  // steps — so a questionnaire holding one is offered here rather than dropped.
  const shown = new Set();
  ["fees", "processing_times", "general_entry"].forEach((topic) => {
    appendTools(container, plan, topic, shown);
    appendDelegates(container, plan, topic, shown);
  });
  return container.childElementCount > before ? container : null;
}

// The standing caveat and when the evidence was read, at the foot of every plan.
// The caveat is about an application, and half of it is false where there is none: there is nothing
// to apply for and no visa to be approved. What still holds is the part that matters most to a
// visa-free traveller — the rules change, and the border decides.
function renderClosingNote(plan) {
  const note = element("section", "plan-closing");
  note.append(
    element(
      "p",
      "disclaimer",
      needsNoVisa(plan)
        ? `Entry rules can change, including which passports need a visa. Confirm the current rules with ${authoritiesSentence(plan)} before you travel. Meeting them does not guarantee entry, which is decided at the border.`
        : `Requirements can change. Confirm the current rules, fees, documents and appointment instructions with ${authoritiesSentence(plan)} before applying. A visa does not guarantee approval or entry.`,
    ),
    element("p", "checked-at", `Evidence last checked ${new Date(plan.last_checked).toLocaleString()}.`),
  );
  return note;
}

// Refusing is a legitimate outcome for high-stakes guidance, so it gets a real explanation
// rather than a generic failure message. Each cause calls for a different next step (TODO item 73),
// and each sentence here must be true of the cause it is shown for: a failed model call is never
// worded as a missing page, and a page we could not read is never described.
const REFUSAL_COPY = {
  not_supported: {
    eyebrow: "Not covered yet",
    title: "This destination isn't covered yet",
    next: "Choose one of the destinations in the list. A country is added once its government's own sites have been confirmed.",
  },
  not_applicable: {
    eyebrow: "Nothing to research",
    title: "No visa to research",
    next: "Choose a different destination, or a different passport.",
  },
  no_official_answer: {
    eyebrow: "No official answer found",
    title: "No official page answered this",
    next: "Check with the destination's embassy or immigration authority directly.",
  },
  pages_unreadable: {
    eyebrow: "Official pages unreadable",
    title: "Official pages could not be read",
    next: "Everything linked above is on the government's own site. We could not read it, so we say nothing about what it contains. Open it yourself.",
  },
  check_failed: {
    eyebrow: "Check could not run",
    title: "The check could not run",
    next: "Nothing was concluded and nothing was saved. Generate the plan again: this is usually momentary.",
    retry: true,
  },
  search_unavailable: {
    eyebrow: "Search unavailable",
    title: "Official sources could not be searched",
    next: "Try again later.",
  },
  internal_error: {
    eyebrow: "Something went wrong",
    title: "Something went wrong on our side",
    next: "Try again later, or send it to us below so we can fix it.",
  },
};

const REFUSAL_FALLBACK = {
  eyebrow: "Evidence unavailable",
  title: "No verified plan",
  next: "Rather than show guidance that may be wrong or out of date, no plan is produced. Try again later, or check the responsible authority directly.",
};

function renderRefusal(detail) {
  const copy = REFUSAL_COPY[detail.cause] || REFUSAL_FALLBACK;
  const { container } = panel(copy.title, copy.eyebrow, "refusal");
  container.append(
    element(
      "p",
      "lead",
      detail.message || "A verified plan could not be produced from official sources.",
    ),
  );

  const pages = detail.unreadable_pages || [];
  if (pages.length) {
    const block = element("div", "reliability-block");
    block.append(element("h3", "", pages.length === 1 ? "The page to open yourself" : "Pages to open yourself"));
    const list = element("ul", "refused-pages");
    pages.forEach((url) => {
      const item = element("li");
      item.append(externalLink(new URL(url).hostname + new URL(url).pathname, url));
      list.append(item);
    });
    block.append(list);
    container.append(block);
  }

  const reasons = detail.reasons || [];
  if (reasons.length) {
    const block = element("div", "reliability-block");
    block.append(element("h3", "", "What could not be verified"));
    const list = element("ul");
    reasons.forEach((reason) => list.append(element("li", "", reason)));
    block.append(list);
    container.append(block);
  }

  container.append(element("p", "disclaimer", copy.next));
  // A check that failed on our side is worth one click to try again: the form still holds the trip,
  // and nothing from the failed run was kept to be served back (entry 258).
  if (copy.retry) {
    const again = element("button", "refusal-retry", "Generate the plan again");
    again.type = "button";
    again.addEventListener("click", () => form.requestSubmit());
    container.append(again);
  }
  results.replaceChildren(container);
}

// The three answers a traveller came for, in one box above the plan, each linking to the section
// that sets it out. Every line is derived from the same fields the section below renders, and says
// no more than it: where a section hedges, the box hedges.
function glanceDecision(plan) {
  if (plan.visa_required === null) {
    return { tone: "uncertain", value: "Could not be confirmed", note: "See why in the visa decision" };
  }
  const condition = plan.decision_condition
    ? `Only if ${plan.decision_condition.replace(/\.$/, "")}`
    : "";
  if (plan.visa_required) {
    return { tone: "visa", value: "Visa required", note: condition || sentenceCase(plan.visa_type) || "" };
  }
  return { tone: "no-visa", value: "No visa required", note: condition };
}

function glanceApply(plan) {
  const location = plan.where_to_apply;
  if (!location) {
    return needsNoVisa(plan)
      ? { value: "Nowhere", note: "No visa, so no application" }
      : { value: "Not confirmed", note: "See what we found" };
  }
  // The authority leads because it is the shortest true thing a plan holds: the location and method
  // are the model's sentences, often a full address, and are clipped here and given in full below.
  // Under it, the stated location, or the method where no location is stated.
  const place = location.location || location.application_method;
  const inPerson = location.in_person === "required" ? "In person" : "";
  return { value: location.authority, note: [inPerson, place].filter(Boolean).join(" · ") };
}

function glanceDocuments(plan, documentsShown) {
  if (needsNoVisa(plan)) return { value: "None to gather", note: "No visa application" };
  if (!documentsShown) {
    return { value: "No checklist found", note: "Not among the official pages we could read" };
  }
  if (plan.requirements.length) {
    return { value: `${plan.requirements.length} listed`, note: "From the official source" };
  }
  if ((plan.application_document_source_ids || []).length) {
    return { value: "Official checklist", note: "Linked from the authority" };
  }
  if ((plan.unavailable_sources || []).some((f) => String(f.source_id).startsWith("checklist_unread_"))) {
    return { value: "Checklist found", note: "We could not read it; open it yourself" };
  }
  if (toolsFor(plan, "document_checklist").length) {
    return { value: "Through a questionnaire", note: "The authority's own" };
  }
  if (delegatesFor(plan, "document_checklist").length) {
    return { value: "Through a contractor", note: "The authority sends applicants there" };
  }
  return { value: "No checklist found", note: "See the documents section" };
}

function renderGlance(plan, documentsShown) {
  const box = element("section", "glance");
  box.setAttribute("aria-label", "At a glance");
  const grid = element("div", "glance-grid");
  const decision = glanceDecision(plan);
  [
    ["Visa", decision, "plan-decision"],
    ["Where to apply", glanceApply(plan), "plan-apply"],
    ["Documents", glanceDocuments(plan, documentsShown), documentsShown ? "plan-documents" : "plan-apply"],
  ].forEach(([label, fact, target]) => {
    const cell = element("a", fact.tone ? `glance-cell glance-cell--${fact.tone}` : "glance-cell");
    cell.href = `#${target}`;
    cell.append(element("span", "glance-label", label), element("strong", "glance-value", fact.value));
    if (fact.note) cell.append(element("span", "glance-note", fact.note));
    cell.append(element("span", "glance-more", "Read more ↓"));
    grid.append(cell);
  });
  box.append(grid);
  return box;
}

// The traveller's own government's travel advice, as a link and nothing else (DECISIONS entry 260).
// No word of the advice is shown: the page names whose it is and where it goes. A government that
// refused us is still linked, and says the link could not be opened from here to check.
function siteOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function renderAdvice(advice, destinationName) {
  if (!advice) return null;
  const box = element("section", "advice");
  box.setAttribute("aria-label", "Your government's travel advice");
  const card = externalLink("", advice.url, "advice-card");
  const body = element("span", "advice-body");
  const label = advice.about_destination ? `Travel advice · ${destinationName}` : "Travel advice";
  body.append(element("span", "advice-label", label));
  body.append(element("strong", "advice-title", advice.government));
  const meta = [`Written for ${advice.written_for}`];
  if (advice.language !== "English") meta.push(`In ${advice.language}`);
  meta.push(siteOf(advice.url));
  body.append(element("span", "advice-meta", meta.join(" · ")));
  if (!advice.about_destination) {
    body.append(element("span", "advice-meta", `Its travel advice page — we found no separate page for ${destinationName}.`));
  }
  if (!advice.checked) {
    body.append(element("span", "advice-note", "This site would not open for us, so we could not check the link."));
  }
  card.append(body, element("span", "advice-arrow", "↗"));
  card.setAttribute("aria-label", `${advice.government}: travel advice${advice.about_destination ? ` for ${destinationName}` : ""} (opens in a new tab)`);
  box.append(card);
  if (advice.english_url) box.append(externalLink("Also published in English ↗", advice.english_url, "advice-alt"));
  return box;
}

async function fetchAdvice(request) {
  try {
    const query = new URLSearchParams({
      passport: request.traveller.passport_nationality,
      destination: request.destination,
    });
    const response = await fetch(`/travel-advice?${query}`, { headers: { Accept: "application/json" } });
    if (!response.ok) return null;
    return (await response.json()).advice || null;
  } catch {
    // The panel is never worth a failed plan: without it the page simply shows none.
    return null;
  }
}

function withId(node, id) {
  if (node) node.id = id;
  return node;
}

function renderPlan(plan, advice = null) {
  const ctx = {
    sourceMap: new Map(plan.sources.map((source) => [source.source_id, source])),
    home: assignSourceHomes(plan),
    seen: new Set(),
    seenUrls: new Set(),
  };
  const decision = withId(renderDecision(plan, ctx), "plan-decision");
  const apply = withId(renderApplicationLocation(plan, ctx), "plan-apply");
  const documents = withId(renderRequirements(plan, ctx), "plan-documents");
  const steps = renderSteps(plan, ctx);
  const evidence = withId(renderReliability(plan), "plan-evidence");
  results.replaceChildren(
    ...[
      renderTrip(),
      renderGlance(plan, Boolean(documents)),
      renderAdvice(advice, optionLabel(destinationSelect, destinationSelect.value)),
      decision,
      apply,
      documents,
      steps,
      evidence,
      renderClosingNote(plan),
    ].filter(Boolean),
  );
}

// What each step of a plan request is called on screen (TODO item 57). The server names a step and
// nothing it found: no part of a plan is shown before the whole plan has been validated, so a claim
// the finished plan might still drop never reaches the traveller (DECISIONS entry 6). The corpus
// and crawl steps share a line because to a traveller they are one thing.
const STAGE_LABELS = {
  search: "Searching the destination's official government sites",
  corpus: "Gathering the official pages already known",
  crawl: "Gathering the official pages already known",
  select: "Choosing which pages to read",
  fetch: "Reading the chosen pages",
  adjudicate: "Checking which page answers each question",
  retrieve: "Collecting the pages the plan will cite",
  write: "Writing the plan from those pages — usually the longest step",
};

let progressTimer;

// The request, the steps seen and what was shown, kept only so a problem report can send them.
let lastRun = null;

function startProgress() {
  // A placeholder until the server names its first step, which then takes its place.
  const starting = element("li", "current", "Starting");
  starting.dataset.placeholder = "true";
  progressSteps.replaceChildren(starting);
  const started = performance.now();
  const tick = () => {
    progressElapsed.textContent = `${Math.round((performance.now() - started) / 1000)}s`;
  };
  tick();
  progressTimer = setInterval(tick, 1000);
  progress.hidden = false;
}

function showStage(stage) {
  if (lastRun) lastRun.stages.push(stage);
  const label = STAGE_LABELS[stage];
  if (!label) return;
  const current = progressSteps.lastElementChild;
  if (current && current.textContent === label) return;
  if (current && current.dataset.placeholder) current.remove();
  else if (current) current.className = "done";
  progressSteps.append(element("li", "current", label));
}

function stopProgress() {
  clearInterval(progressTimer);
  progress.hidden = true;
}

// The stream is one JSON object per line: `stage` events as each step starts, then one outcome.
async function* streamedEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffered += decoder.decode(value, { stream: true });
    let newline;
    while ((newline = buffered.indexOf("\n")) >= 0) {
      const line = buffered.slice(0, newline);
      buffered = buffered.slice(newline + 1);
      if (line.trim()) yield JSON.parse(line);
    }
  }
  if (buffered.trim()) yield JSON.parse(buffered);
}

function showRefusal(detail) {
  if (detail.sign_in) {
    // Not a refusal about evidence: nothing was researched, because nobody is signed in — or the
    // session expired since the page loaded.
    const link = element("a", "", "Sign in with Ofself");
    link.href = "/oauth/login";
    errorMessage.replaceChildren(link, " to generate a plan.");
    errorMessage.hidden = false;
    return;
  }
  // A refusal names the evidence it could not verify, rather than failing opaquely.
  renderRefusal(detail);
  attachReport("refusal", detail);
  results.scrollIntoView({ behavior: "smooth", block: "start" });
}

function optionLabel(select, value) {
  const option = [...select.options].find((candidate) => candidate.value === value);
  return option ? option.textContent.trim() : value;
}

// TODO item 74. A refused run is sent with one tap: the run is the whole report. A plan asks what
// is wrong, because a working run alone does not say. Either way the traveller is told what is sent:
// the trip, what this page showed and our log of the run. The server attaches the log; the page
// cannot. Nothing is offered where no run could be fixed.
const NOTHING_TO_FIX = new Set(["not_applicable", "not_supported"]);

// A plan that hands the decision to the authority itself is working as intended: its official
// questionnaire decides (entry 59), or its page refused us and may never be worked around (entry 27).
function handsDecisionToAuthority(plan) {
  if ((plan.official_tools || []).some((tool) => tool.topic === "visa_decision")) return true;
  return plan.visa_required === null
    && (plan.unavailable_sources || []).some((failure) => failure.may_hold_decision);
}

function tripLine(request) {
  const traveller = request.traveller;
  return [
    optionLabel(destinationSelect, request.destination),
    `${optionLabel(nationalitySelect, traveller.passport_nationality)} passport`,
    `from ${[traveller.region_of_residence, optionLabel(residenceSelect, traveller.country_of_residence)]
      .filter(Boolean)
      .join(", ")}`,
    traveller.travel_purpose,
  ].join(" · ");
}

async function sendReport(run) {
  const response = await fetch("/reports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(run),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error((payload.detail && payload.detail.message) || "It could not be sent. Try again.");
  return payload.report_id;
}

function attachReport(outcome, shown) {
  if (!lastRun) return;
  if (outcome === "refusal" && NOTHING_TO_FIX.has(shown.cause)) return;
  if (outcome === "plan" && handsDecisionToAuthority(shown)) return;
  const run = { request: lastRun.request, stages: [...lastRun.stages], outcome, shown };
  const box = element("section", outcome === "plan" ? "report" : "report report--fix");
  if (outcome === "plan") planReport(run, box);
  else fixRequest(run, box);
  results.append(box);
}

function fixRequest(run, box) {
  const text = element("div", "report-text");
  text.append(
    element("h3", "", "Help us fix this result"),
    element("p", "", "One tap sends us this result and how it was researched, so we can fix it for trips like yours."),
    element("p", "report-note", `Sends: ${tripLine(run.request)}. Nothing from your Ofself account.`),
  );
  const status = element("p", "report-status");
  status.hidden = true;
  text.append(status);
  const send = element("button", "report-fix", "Send it to us");
  send.type = "button";
  send.addEventListener("click", async () => {
    send.disabled = true;
    send.textContent = "Sending…";
    status.hidden = true;
    try {
      await sendReport(run);
      box.replaceChildren(element("p", "report-sent", "Thank you — it's with us, and we'll work on fixing it."));
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : "It could not be sent. Try again.";
      status.hidden = false;
      send.disabled = false;
      send.textContent = "Send it to us";
    }
  });
  box.append(text, send);
}

function planReport(run, box) {
  const open = element("button", "report-open", "Tell us what's wrong");
  open.type = "button";
  open.addEventListener("click", () => box.replaceChildren(planReportForm(run, box)));
  box.replaceChildren(element("p", "report-intro", "Something wrong or missing in this plan?"), open);
}

function planReportForm(run, box) {
  const card = element("div", "report-card");
  const label = element("label", "report-label", "What's wrong?");
  label.htmlFor = "report-message";
  const message = element("textarea", "report-message");
  message.id = "report-message";
  message.rows = 3;
  message.maxLength = 2000;
  message.placeholder = "For example: the visa decision is wrong, a link is broken, a step is missing";
  const status = element("p", "report-status");
  status.hidden = true;
  const send = element("button", "report-send", "Send");
  send.type = "button";
  send.disabled = true;
  message.addEventListener("input", () => {
    send.disabled = !message.value.trim();
  });
  const back = element("button", "report-cancel", "Cancel");
  back.type = "button";
  back.addEventListener("click", () => planReport(run, box));
  send.addEventListener("click", async () => {
    send.disabled = true;
    back.disabled = true;
    try {
      await sendReport({ ...run, message: message.value.trim() });
      box.replaceChildren(element("p", "report-sent", "Thank you — it's with us, and we'll look into it."));
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : "It could not be sent. Try again.";
      status.hidden = false;
      send.disabled = false;
      back.disabled = false;
    }
  });
  const actions = element("div", "report-actions");
  actions.append(send, back);
  card.append(
    label,
    message,
    element(
      "p",
      "report-note",
      `We also get your trip (${tripLine(run.request)}), this plan and how it was researched. `
        + "Nothing from your Ofself account; please leave personal details out of your message.",
    ),
    status,
    actions,
  );
  return card;
}

async function generatePlan(event) {
  event.preventDefault();
  errorMessage.hidden = true;
  startProgress();
  results.setAttribute("aria-busy", "true");
  generateButton.disabled = true;

  try {
    const request = {
      destination: destinationSelect.value,
      traveller: {
        passport_nationality: nationalitySelect.value,
        country_of_residence: residenceSelect.value,
        region_of_residence: regionGroup.hidden ? null : regionSelect.value || null,
        travel_purpose: purposeSelect.value,
      },
    };
    lastRun = { request, stages: [] };
    // Looked up beside the plan rather than after it, and shown with it: a lookup in committed
    // data, so it is ready long before the plan (entry 260).
    const advicePromise = fetchAdvice(request);
    const response = await fetch("/visa-plans/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    if (!response.ok) {
      // Turned away before anything was researched: signed out, or a corridor with no answer.
      const payload = await response.json();
      showRefusal(payload.detail || {});
      return;
    }
    for await (const streamed of streamedEvents(response)) {
      if (streamed.event === "stage") {
        showStage(streamed.stage);
      } else if (streamed.event === "plan") {
        renderPlan(streamed.plan, await advicePromise);
        attachReport("plan", streamed.plan);
        results.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      } else if (streamed.event === "refusal") {
        showRefusal(streamed.detail || {});
        return;
      } else if (streamed.event === "error") {
        showRefusal({
          cause: "internal_error",
          message: "The plan could not be generated because of a fault on our side. Nothing was concluded about this trip.",
        });
        return;
      }
    }
    throw new Error("The connection closed before the plan arrived. Try again.");
  } catch (error) {
    errorMessage.textContent = error instanceof Error ? error.message : "The plan could not be generated.";
    errorMessage.hidden = false;
  } finally {
    stopProgress();
    results.setAttribute("aria-busy", "false");
    generateButton.disabled = false;
  }
}

form.addEventListener("submit", generatePlan);

// Signed in with Ofself, the form starts from what the traveller's account holds (TODO item 55,
// DECISIONS entries 180 and 181): their passports, a residence permit, and journeys they are
// considering. Every part is a default they confirm. One is filled in and stays editable; several
// are offered with none chosen, because a dual national's answer depends on which passport the
// trip is on; none leaves the field to them. Nothing here is ever the default traveller.
//
// An empty answer never becomes "you have none": Ofself answers a schema this app was not granted
// exactly as it answers one with nothing recorded.

const destinationNote = document.querySelector("#destination-note");
const destinationChoices = document.querySelector("#destination-choices");
const residenceNote = document.querySelector("#residence-note");
const residenceChoices = document.querySelector("#residence-choices");
const purposeNote = document.querySelector("#purpose-note");
const ofselfNote = document.querySelector("#ofself-note");

function countryName(code) {
  const option = nationalitySelect.querySelector(`option[value="${code}"]`);
  return option ? option.textContent.trim() : code;
}

// A note keeps its own style — a field's note or the form's — and only its tone changes.
function showNote(note, parts, tone = "") {
  const base = note.dataset.base || (note.dataset.base = note.classList[0]);
  note.replaceChildren(...parts);
  note.className = tone ? `${base} ${base}--${tone}` : base;
  note.hidden = parts.length === 0;
}

function formatDate(value) {
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function markChoice(container, key) {
  container.querySelectorAll("button").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.key === key));
  });
}

// Choices are offered with none pressed; picking one fills the field and says what it came with.
function offerChoices(container, choices, onPick) {
  container.replaceChildren(
    ...choices.map((choice) => {
      const button = element("button", "field-choice", choice.label);
      button.type = "button";
      button.dataset.key = choice.key;
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", () => {
        markChoice(container, choice.key);
        onPick(choice.value);
      });
      return button;
    }),
  );
  container.hidden = choices.length === 0;
}

// A date the person typed in may raise a question and never close one, so the note says which
// kind of date it is, and an expired document is said plainly.
function expirySentence(record, noun) {
  if (!record.expires_at) return { text: "", tone: "" };
  const when = formatDate(record.expires_at);
  if (record.expired) {
    return { text: ` Ofself records that this ${noun} expired on ${when}.`, tone: "warn" };
  }
  if (record.expiry_attested) {
    return { text: ` Expires ${when}, read from the ${noun} itself.`, tone: "" };
  }
  return {
    text: ` Expires ${when}, as entered in Ofself — check it against the ${noun} itself.`,
    tone: "",
  };
}

function passportSentences(payload) {
  const notes = [];
  for (const withheld of payload.withheld_passports || []) {
    const name = withheld.label || (withheld.nationality ? `${countryName(withheld.nationality)} passport` : "A passport");
    notes.push(
      ` ${name} (document code ${withheld.document_code}) is not offered: this app researches ordinary passports only.`,
    );
  }
  if (payload.other_holders) {
    notes.push(
      ` ${payload.other_holders === 1 ? "One travel document belongs" : `${payload.other_holders} travel documents belong`} to someone else on your account and ${payload.other_holders === 1 ? "is" : "are"} not offered.`,
    );
  }
  if ((payload.unrecognised || []).length) {
    notes.push(
      ` It also lists ${payload.unrecognised.map((value) => `“${value}”`).join(", ")}, which this app has no country data for.`,
    );
  }
  if (payload.encrypted_values) {
    notes.push(" Some of it is encrypted, and this app does not read encrypted details.");
  }
  return notes.join("");
}

function describePassport(passport, lead, extra) {
  const expiry = passport.from_document ? expirySentence(passport, "passport") : { text: "", tone: "" };
  showNote(passportNote, [`${lead}${expiry.text}${extra}`], expiry.tone);
}

// The Ofself passport this trip is on, when there is one: its expiry is set beside the trip's dates.
let chosenPassport = null;

function prefillPassport(payload) {
  const passports = payload.passports || [];
  const extra = passportSentences(payload);
  const pick = (passport) => {
    nationalitySelect.value = passport.nationality;
    chosenPassport = passport;
    describePassport(passport, "From your Ofself account. Change it if this trip is on another passport.", extra);
  };
  if (passports.length === 1) {
    pick(passports[0]);
  } else if (passports.length > 1) {
    offerChoices(
      passportChoices,
      passports.map((passport) => ({
        key: passport.nationality,
        label: passport.label || countryName(passport.nationality),
        value: passport,
      })),
      pick,
    );
    showNote(passportNote, [
      `Your Ofself account lists ${passports.length} passports or citizenships. Choose the one this trip is on.${extra}`,
    ]);
  } else if (extra) {
    // Nothing to offer; what was withheld or unreadable is still said. That nothing was shared at
    // all is said once, above the form.
    showNote(passportNote, [extra.trim()]);
  }
  nationalitySelect.addEventListener("change", () => {
    markChoice(passportChoices, nationalitySelect.value);
    const chosen = passports.find((passport) => passport.nationality === nationalitySelect.value);
    chosenPassport = chosen || null;
    if (chosen) describePassport(chosen, "From your Ofself account.", extra);
    else showNote(passportNote, []);
    refreshTripStrip();
  });
}

function prefillResidence(payload) {
  // A permit that has expired is still shown, marked, but a current one is offered first.
  const residences = [...(payload.residences || [])].sort((a, b) => Number(a.expired) - Number(b.expired));
  const describe = (residence) => {
    const kind = residence.permit_class ? `residence permit (${residence.permit_class})` : "residence permit";
    const expiry = expirySentence(residence, "permit");
    showNote(
      residenceNote,
      [`From your ${kind} in Ofself. Change it if you apply from somewhere else.${expiry.text}`],
      expiry.tone,
    );
  };
  const pick = (residence) => {
    residenceSelect.value = residence.country;
    loadRegions();
    describe(residence);
  };
  if (residences.length === 1) {
    pick(residences[0]);
  } else if (residences.length > 1) {
    offerChoices(
      residenceChoices,
      residences.map((residence, index) => ({
        key: `${residence.country}-${index}`,
        label: residence.label || countryName(residence.country),
        value: residence,
      })),
      pick,
    );
    showNote(residenceNote, ["Your Ofself account lists more than one residence permit. Choose where you apply from."]);
  }
}

function destinationLabel(select, slug) {
  const option = select.querySelector(`option[value="${slug}"]`);
  return option ? option.textContent.trim() : slug;
}

function windowSentence(plan) {
  if (!plan.earliest && !plan.latest) return "";
  if (plan.earliest && plan.latest) return `, ${formatDate(plan.earliest)} to ${formatDate(plan.latest)}`;
  return `, ${formatDate(plan.earliest || plan.latest)}`;
}

function prefillDestination(payload) {
  const choices = [];
  for (const plan of payload.plans || []) {
    for (const candidate of plan.candidates) {
      // A destination this page cannot research is not offered at all.
      const option = destinationSelect.querySelector(`option[value="${candidate.destination_slug}"]`);
      if (!option || option.disabled) continue;
      choices.push({ plan, candidate });
    }
  }
  const unresolved = payload.unresolved_candidates
    ? ` ${payload.unresolved_candidates === 1 ? "One place" : `${payload.unresolved_candidates} places`} in your plans could not be matched to a country.`
    : "";

  const pick = ({ plan, candidate }) => {
    destinationSelect.value = candidate.destination_slug;
    showNote(destinationNote, [`From your Ofself plan “${plan.label}”${windowSentence(plan)}.${unresolved}`]);
    prefillWindow(plan);
    if (candidate.purpose) {
      purposeSelect.value = candidate.purpose;
      showNote(purposeNote, ["From the same plan."]);
    } else if (candidate.recorded_purpose) {
      showNote(
        purposeNote,
        [`Your plan says “${candidate.recorded_purpose}”, which this app does not research. Choose the closest purpose.`],
        "warn",
      );
    } else {
      showNote(purposeNote, []);
    }
  };

  if (choices.length === 1) {
    pick(choices[0]);
  } else if (choices.length > 1) {
    offerChoices(
      destinationChoices,
      choices.map((choice, index) => ({
        key: String(index),
        label: `${destinationLabel(destinationSelect, choice.candidate.destination_slug)} · ${choice.plan.label}`,
        value: choice,
      })),
      pick,
    );
    showNote(destinationNote, [`From your Ofself plans. Choose a destination to research.${unresolved}`]);
  } else if (unresolved) {
    showNote(destinationNote, [unresolved.trim()]);
  }
  destinationSelect.addEventListener("change", () => {
    markChoice(destinationChoices, "");
    showNote(destinationNote, []);
    showNote(purposeNote, []);
  });
}

async function prefillFromOfself() {
  if (document.body.dataset.signedIn !== "true") return;

  let response;
  let payload = {};
  try {
    response = await fetch("/oauth/traveller", { headers: { Accept: "application/json" } });
    payload = await response.json();
  } catch {
    showNote(ofselfNote, ["Your Ofself account could not be reached. Fill the form in yourself."], "warn");
    return;
  }

  if (!response.ok) {
    const detail = payload.detail || {};
    if (detail.reconnect || detail.sign_in) {
      const link = element("a", "", "Reconnect with Ofself");
      link.href = "/oauth/login";
      showNote(
        ofselfNote,
        ["This app no longer has access to your Ofself account. ", link, " or fill the form in yourself."],
        "warn",
      );
      return;
    }
    showNote(
      ofselfNote,
      [`${detail.message || "Your Ofself details could not be read."} Fill the form in yourself.`],
      "warn",
    );
    return;
  }

  prefillDestination(payload);
  prefillPassport(payload);
  prefillResidence(payload);
  showNote(ofselfNote, [formSentence(payload)]);
}

// One statement for the whole form, so an empty field does not each say the same thing. It never
// says the traveller has no documents or plans: Ofself answers a schema this app was not granted
// exactly as it answers one with nothing recorded.
function formSentence(payload) {
  const found =
    (payload.passports || []).length + (payload.residences || []).length + (payload.plans || []).length;
  if (!found) return "Nothing shared from your Ofself account fills this form yet. Fill it in yourself.";
  return "Fields filled from your Ofself account say so. Check them, and fill in the rest.";
}

// Travel dates (TODO item 80, DECISIONS entry 256). Optional, and kept in this page: nothing here is
// sent with the plan request, so a plan without dates is exactly today's plan and stored research is
// reused as before. The dates shape what is shown beside the plan, never what the plan concludes.
const datesExact = document.querySelector("#dates-exact");
const datesRough = document.querySelector("#dates-rough");
const datesDepart = document.querySelector("#dates-depart");
const datesReturn = document.querySelector("#dates-return");
const datesFromMonth = document.querySelector("#dates-from-month");
const datesToMonth = document.querySelector("#dates-to-month");
const datesNote = document.querySelector("#dates-note");
const routeDates = document.querySelector("#route-dates");
const DAY_MS = 86_400_000;
let datesFromOfself = "";

function today() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function isoDay(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function parseDay(value) {
  return value ? new Date(`${value}T00:00:00`) : null;
}

function monthValue(date) {
  return isoDay(date).slice(0, 7);
}

function monthLabel(date, withYear = true) {
  return date.toLocaleDateString(undefined, withYear ? { month: "long", year: "numeric" } : { month: "long" });
}

function dayLabel(date, withYear = true) {
  return date.toLocaleDateString(
    undefined,
    withYear ? { day: "numeric", month: "short", year: "numeric" } : { day: "numeric", month: "short" },
  );
}

// Two years of months, from this one: "roughly" is a month or a span of them.
function fillMonths() {
  const first = today();
  datesToMonth.append(new Option("Same month", ""));
  for (let offset = 0; offset < 24; offset += 1) {
    const month = new Date(first.getFullYear(), first.getMonth() + offset, 1);
    datesFromMonth.append(new Option(monthLabel(month), monthValue(month)));
    datesToMonth.append(new Option(monthLabel(month), monthValue(month)));
  }
}

function datesMode() {
  return form.querySelector('input[name="dates-mode"]:checked').value;
}

function setDatesMode(mode) {
  form.querySelector(`#dates-mode-${mode}`).checked = true;
}

// The trip as this page holds it, or null. A rough span runs from the first of its first month to
// the last day of its last, so a passport is measured against the latest the trip could end.
function tripDates() {
  const mode = datesMode();
  if (mode === "exact") {
    const start = parseDay(datesDepart.value);
    const end = parseDay(datesReturn.value);
    if (!start || !end || start < today() || end < start) return null;
    return { mode, start, end };
  }
  if (mode === "rough" && datesFromMonth.value) {
    const from = datesFromMonth.value;
    const to = datesToMonth.value && datesToMonth.value > from ? datesToMonth.value : from;
    const [fromYear, fromMonth] = from.split("-").map(Number);
    const [toYear, toMonth] = to.split("-").map(Number);
    return { mode, start: new Date(fromYear, fromMonth - 1, 1), end: new Date(toYear, toMonth, 0) };
  }
  return null;
}

function tripRange(trip) {
  if (trip.mode === "exact") {
    const sameYear = trip.start.getFullYear() === trip.end.getFullYear();
    return `${dayLabel(trip.start, !sameYear)} – ${dayLabel(trip.end)}`;
  }
  if (monthValue(trip.start) === monthValue(trip.end)) return monthLabel(trip.start);
  const sameYear = trip.start.getFullYear() === trip.end.getFullYear();
  return `${monthLabel(trip.start, !sameYear)} – ${monthLabel(trip.end)}`;
}

function tripLength(trip) {
  if (trip.mode !== "exact") return "";
  const nights = Math.round((trip.end - trip.start) / DAY_MS);
  return nights === 1 ? "1 night" : `${nights} nights`;
}

// Shows the part of the field the mode needs, keeps the date pickers to possible dates, and says
// back what was chosen — or where it came from, until the traveller changes it.
function updateDates() {
  const mode = datesMode();
  datesExact.hidden = mode !== "exact";
  datesRough.hidden = mode !== "rough";
  datesDepart.min = isoDay(today());
  datesReturn.min = datesDepart.value || isoDay(today());
  datesDepart.required = mode === "exact";
  datesReturn.required = mode === "exact";
  datesDepart.setCustomValidity(
    mode === "exact" && datesDepart.value && parseDay(datesDepart.value) < today()
      ? "Choose a day from today on."
      : "",
  );
  datesReturn.setCustomValidity(
    mode === "exact" && datesDepart.value && datesReturn.value && datesReturn.value < datesDepart.value
      ? "Choose a return day after you leave."
      : "",
  );

  const trip = tripDates();
  routeDates.hidden = !trip;
  routeDates.textContent = trip ? ` · ${tripRange(trip)}` : "";
  if (datesFromOfself && mode === "rough") {
    showNote(datesNote, [datesFromOfself]);
  } else if (trip) {
    const length = tripLength(trip);
    const sameYear = trip.start.getFullYear() === trip.end.getFullYear();
    const rough =
      monthValue(trip.start) === monthValue(trip.end)
        ? `Sometime in ${tripRange(trip)}.`
        : `Sometime between ${monthLabel(trip.start, !sameYear)} and ${monthLabel(trip.end)}.`;
    showNote(datesNote, [length ? `${length}.` : rough]);
  } else {
    showNote(datesNote, []);
  }
  refreshTripStrip();
}

function weeksOrDays(days) {
  if (days <= 0) return "You leave today";
  if (days < 14) return `You leave in ${days} ${days === 1 ? "day" : "days"}`;
  return `You leave in ${Math.round(days / 7)} weeks`;
}

function countdown(trip) {
  const days = Math.round((trip.start - today()) / DAY_MS);
  if (trip.mode === "exact") return weeksOrDays(days);
  const months =
    (trip.start.getFullYear() - today().getFullYear()) * 12 + trip.start.getMonth() - today().getMonth();
  if (months <= 0) return "This month";
  return months === 1 ? "Next month" : `About ${months} months away`;
}

function wholeMonthsBetween(from, to) {
  let months = (to.getFullYear() - from.getFullYear()) * 12 + to.getMonth() - from.getMonth();
  if (to.getDate() < from.getDate()) months -= 1;
  return months;
}

// The traveller's own passport beside their own dates: a fact, never a ruling. Whether it is long
// enough is the destination's rule, which the plan states and this line does not (entry 256).
function passportLine(trip) {
  const passport = chosenPassport;
  if (!passport || !passport.expires_at || passport.nationality !== nationalitySelect.value) return null;
  const expiry = parseDay(passport.expires_at);
  const back = trip.mode === "exact" ? "you return" : `the end of ${monthLabel(trip.end)}`;
  const typed = passport.expiry_attested ? "" : " That date was entered in Ofself; check it against the passport.";
  if (expiry < trip.end) {
    return { text: `Your passport expires on ${dayLabel(expiry)}, before ${back}.${typed}`, tone: "warn" };
  }
  const months = wholeMonthsBetween(trip.end, expiry);
  const gap =
    months < 1 ? "less than a month" : months === 1 ? "1 month" : `${months} months`;
  return {
    text: `Your passport expires on ${dayLabel(expiry)}, ${gap} after ${back}. Compare it with the passport rule in the plan.${typed}`,
    tone: "",
  };
}

function renderTrip() {
  const trip = tripDates();
  if (!trip) return null;
  const strip = element("section", "trip-strip");
  strip.setAttribute("aria-label", "Your trip");
  const head = element("p", "trip-strip-head");
  head.append(element("span", "trip-strip-label", "Your trip"), element("strong", "", tripRange(trip)));
  const length = tripLength(trip);
  if (length) head.append(element("span", "", length));
  head.append(element("span", "trip-strip-countdown", countdown(trip)));
  strip.append(head);
  const passport = passportLine(trip);
  if (passport) {
    strip.append(element("p", passport.tone ? `trip-strip-passport trip-strip-passport--${passport.tone}` : "trip-strip-passport", passport.text));
  }
  return strip;
}

// A plan already on screen follows the dates as they change; nothing is shown before a plan.
function refreshTripStrip() {
  if (!results.querySelector(".glance")) return;
  const old = results.querySelector(".trip-strip");
  const fresh = renderTrip();
  if (old && fresh) old.replaceWith(fresh);
  else if (old) old.remove();
  else if (fresh) results.prepend(fresh);
}

// A trip idea's window is soft — the schema says never to promote it to firm dates — so it fills
// "Roughly", as months, and only while the traveller has not chosen dates themselves.
function prefillWindow(plan) {
  if (datesMode() !== "unsure" || (!plan.earliest && !plan.latest)) return;
  const first = parseDay(plan.earliest || plan.latest);
  const last = parseDay(plan.latest || plan.earliest);
  if (monthValue(last) < monthValue(today())) return;
  setDatesMode("rough");
  datesFromMonth.value = monthValue(first < today() ? today() : first);
  datesToMonth.value = monthValue(last) > datesFromMonth.value ? monthValue(last) : "";
  datesFromOfself = `From your Ofself plan “${plan.label}”, as a rough window. Change it if you know your dates.`;
  updateDates();
}

const datesField = document.querySelector("#dates-group");
datesField.addEventListener("input", () => {
  datesFromOfself = "";
  updateDates();
});
datesField.addEventListener("change", () => {
  datesFromOfself = "";
  updateDates();
});
fillMonths();
updateDates();

const routeFrom = document.querySelector("#route-from");
const routeTo = document.querySelector("#route-to");
const routePassport = document.querySelector("#route-passport");
const routePurpose = document.querySelector("#route-purpose");

function chosenLabel(select) {
  const option = select.selectedOptions[0];
  return option && option.value ? option.textContent.trim() : "—";
}

// The ticket restates the form. Prefill and the choice buttons set values without a change event,
// so it is refreshed on any change or click in the form, and once the Ofself prefill settles.
function updateRoute() {
  routeFrom.textContent = chosenLabel(residenceSelect);
  routeTo.textContent = chosenLabel(destinationSelect);
  routePassport.textContent = chosenLabel(nationalitySelect);
  routePurpose.textContent = chosenLabel(purposeSelect);
}

// The region a post's jurisdiction is drawn in (TODO item 71, DECISIONS entry 252). Only the chosen
// country's regions are offered, from committed reference data; a country with none hides the field.
let regionsShownFor = 0;
async function loadRegions(keep = "") {
  const country = residenceSelect.value;
  const asked = ++regionsShownFor;
  regionSelect.replaceChildren(new Option("Not given", ""));
  regionGroup.hidden = true;
  if (!country) return;
  try {
    const response = await fetch(`/regions/${encodeURIComponent(country)}`);
    if (!response.ok) return;
    const { regions } = await response.json();
    // A later country choice may have answered first; only the latest one may fill the field.
    if (asked !== regionsShownFor || !regions.length) return;
    regions.forEach((region) => regionSelect.append(new Option(region, region, false, region === keep)));
    regionGroup.hidden = false;
  } catch {
    // Optional detail: without it the plan names the posts and asks, as it did before.
  }
}

residenceSelect.addEventListener("change", () => loadRegions());
loadRegions(regionSelect.dataset.selected);

form.addEventListener("change", updateRoute);
form.addEventListener("click", () => requestAnimationFrame(updateRoute));

updateRoute();
prefillFromOfself().finally(updateRoute);
