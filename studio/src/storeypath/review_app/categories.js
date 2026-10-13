// The categories of item types (catalogue.py CATEGORIES, format 0.10), in their order: what
// each is called, in English and Arabic, its icon, and its side: furniture, or the building's
// equipment. The Item types page and Review's box to find what to place show them so.

export const CATEGORIES = [
  { key: "workstations", name: "Workstations", ar: "مكاتب العمل", icon: "lamp-desk", side: "furniture" },
  { key: "seating", name: "Seating", ar: "الكراسي", icon: "armchair", side: "furniture" },
  { key: "tables", name: "Tables", ar: "الطاولات", icon: "users", side: "furniture" },
  { key: "storage", name: "Storage", ar: "التخزين", icon: "archive", side: "furniture" },
  { key: "soft_seating", name: "Soft seating", ar: "الأرائك", icon: "sofa", side: "furniture" },
  { key: "beds", name: "Beds", ar: "الأسرّة", icon: "bed-double", side: "furniture" },
  { key: "furniture", name: "Other furniture", ar: "أثاث آخر", icon: "package", side: "furniture" },
  { key: "security", name: "Security", ar: "الأمن", icon: "cctv", side: "equipment" },
  { key: "fire_safety", name: "Fire and life safety", ar: "السلامة من الحريق", icon: "flame", side: "equipment" },
  { key: "it_av", name: "IT and AV", ar: "تقنية المعلومات والصوتيات", icon: "monitor", side: "equipment" },
  { key: "building_services", name: "Building services", ar: "خدمات المبنى", icon: "fan", side: "equipment" },
  { key: "appliances", name: "Appliances", ar: "الأجهزة", icon: "refrigerator", side: "equipment" },
  { key: "equipment", name: "Other equipment", ar: "معدات أخرى", icon: "box", side: "equipment" },
];
export const SIDES = { furniture: "Furniture", equipment: "Building equipment" };

const byKey = new Map(CATEGORIES.map((c) => [c.key, c]));
const OLD = { appliance: "appliances" }; // 0.9's, read as

/** A category (of CATEGORIES) by its key; one not known (a later format's): "Other …". */
export function categoryOf(key) {
  return byKey.get(OLD[key] ?? key) ?? { key, name: String(key ?? "").replaceAll("_", " ").replace(/^./, (x) => x.toUpperCase()),
    ar: "", icon: "box", side: "equipment" };
}

export const categoryName = (key) => categoryOf(key).name;
