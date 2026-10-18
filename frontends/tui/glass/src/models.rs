// The model picker across providers: every connected provider's models from
// the core's `catalog` command, grouped by provider, with a switch for which
// model is being chosen (smart or fast). Choosing sends `models PROVIDER FAST
// SMART`: a model of another provider switches the provider, and the other
// role keeps the model that provider already has.

use serde_json::Value;

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Role {
    Smart,
    Fast,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ProviderModels {
    pub id: String,
    pub label: String,
    pub current: bool,
    pub fast: String,
    pub smart: String,
    pub models: Vec<String>,
}

impl ProviderModels {
    fn chosen(&self, role: Role) -> &str {
        match role {
            Role::Smart => &self.smart,
            Role::Fast => &self.fast,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Row {
    /// A provider's name, by its index.
    Header(usize),
    /// (provider index, model index).
    Model(usize, usize),
}

pub struct ModelPicker {
    pub providers: Vec<ProviderModels>,
    pub role: Role,
    pub query: String,
    /// Index into `rows()` of the highlighted model.
    pub selected: usize,
    pub scroll: usize,
}

impl ModelPicker {
    /// Reads the `effects` of a `catalog` result; `None` when it lists nothing.
    pub fn from_catalog(effects: &Value) -> Option<ModelPicker> {
        let providers: Vec<ProviderModels> = effects
            .get("providers")?
            .as_array()?
            .iter()
            .filter_map(|entry| {
                let text = |key: &str| entry.get(key).and_then(Value::as_str).unwrap_or("").to_string();
                let chosen = |role: &str| {
                    entry.get("chosen").and_then(|c| c.get(role)).and_then(Value::as_str).unwrap_or("").to_string()
                };
                let models: Vec<String> = entry
                    .get("models")?
                    .as_array()?
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect();
                let id = text("provider");
                (!id.is_empty() && !models.is_empty()).then(|| ProviderModels {
                    label: if text("label").is_empty() { id.clone() } else { text("label") },
                    id,
                    current: entry.get("current").and_then(Value::as_bool).unwrap_or(false),
                    fast: chosen("fast"),
                    smart: chosen("smart"),
                    models,
                })
            })
            .collect();
        if providers.is_empty() {
            return None;
        }
        let mut picker = ModelPicker { providers, role: Role::Smart, query: String::new(), selected: 0, scroll: 0 };
        picker.select_current();
        Some(picker)
    }

    pub fn rows(&self) -> Vec<Row> {
        let needle = self.query.to_lowercase();
        let mut rows = Vec::new();
        for (p, provider) in self.providers.iter().enumerate() {
            let label_matches = provider.label.to_lowercase().contains(&needle);
            let models: Vec<usize> = provider
                .models
                .iter()
                .enumerate()
                .filter(|(_, model)| needle.is_empty() || label_matches || model.to_lowercase().contains(&needle))
                .map(|(m, _)| m)
                .collect();
            if models.is_empty() {
                continue;
            }
            rows.push(Row::Header(p));
            rows.extend(models.into_iter().map(|m| Row::Model(p, m)));
        }
        rows
    }

    /// Highlights the model the current provider uses for the chosen role.
    fn select_current(&mut self) {
        let rows = self.rows();
        let current = rows.iter().position(|row| match row {
            Row::Model(p, m) => {
                let provider = &self.providers[*p];
                provider.current && provider.models[*m] == provider.chosen(self.role)
            }
            Row::Header(_) => false,
        });
        self.selected = current.or_else(|| first_model(&rows)).unwrap_or(0);
        self.scroll = 0;
    }

    pub fn toggle_role(&mut self) {
        self.role = match self.role {
            Role::Smart => Role::Fast,
            Role::Fast => Role::Smart,
        };
        self.select_current();
    }

    pub fn set_role(&mut self, role: Role) {
        if self.role != role {
            self.toggle_role();
        }
    }

    pub fn step(&mut self, delta: i32) {
        let rows = self.rows();
        let models: Vec<usize> = rows.iter().enumerate().filter(|(_, r)| matches!(r, Row::Model(..))).map(|(i, _)| i).collect();
        let Some(at) = models.iter().position(|&i| i == self.selected) else {
            self.selected = models.first().copied().unwrap_or(0);
            return;
        };
        let next = (at as i32 + delta).clamp(0, models.len() as i32 - 1) as usize;
        self.selected = models[next];
    }

    pub fn select_row(&mut self, row: usize) {
        if matches!(self.rows().get(row), Some(Row::Model(..))) {
            self.selected = row;
        }
    }

    pub fn type_char(&mut self, c: char) {
        self.query.push(c);
        self.refilter();
    }

    pub fn backspace(&mut self) {
        self.query.pop();
        self.refilter();
    }

    fn refilter(&mut self) {
        self.selected = first_model(&self.rows()).unwrap_or(0);
        self.scroll = 0;
    }

    /// Keeps the highlighted row inside a list `visible` rows tall.
    pub fn follow(&mut self, visible: usize) {
        if self.selected < self.scroll {
            // Keep the provider's name in view above its first model.
            self.scroll = self.selected.saturating_sub(1);
        }
        if visible > 0 && self.selected >= self.scroll + visible {
            self.scroll = self.selected + 1 - visible;
        }
    }

    /// The `models` arguments for the highlighted model: provider, fast, smart.
    pub fn choice(&self) -> Option<[String; 3]> {
        let Some(Row::Model(p, m)) = self.rows().get(self.selected).copied() else { return None };
        let provider = &self.providers[p];
        let picked = provider.models[m].clone();
        // The other role keeps what this provider already uses, or takes the
        // same model when the provider has none chosen yet.
        let keep = |role: Role| match provider.chosen(role) {
            "" => picked.clone(),
            model => model.to_string(),
        };
        let (fast, smart) = match self.role {
            Role::Smart => (keep(Role::Fast), picked.clone()),
            Role::Fast => (picked.clone(), keep(Role::Smart)),
        };
        Some([provider.id.clone(), fast, smart])
    }
}

fn first_model(rows: &[Row]) -> Option<usize> {
    rows.iter().position(|row| matches!(row, Row::Model(..)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn picker() -> ModelPicker {
        ModelPicker::from_catalog(&json!({"providers": [
            {"provider": "chatgpt", "label": "ChatGPT", "current": true,
             "chosen": {"fast": "gpt-6.1-sol", "smart": "gpt-6-astra"},
             "models": ["gpt-5.6-luna", "gpt-6-astra", "gpt-6.1-sol"]},
            {"provider": "deepseek", "label": "DeepSeek", "current": false,
             "chosen": {"fast": "deepseek-flash", "smart": "deepseek-v4-pro"},
             "models": ["deepseek-flash", "deepseek-v4-pro"]},
            {"provider": "empty", "label": "Empty", "models": []},
        ]}))
        .unwrap()
    }

    fn highlighted(p: &ModelPicker) -> String {
        match p.rows()[p.selected] {
            Row::Model(provider, model) => p.providers[provider].models[model].clone(),
            Row::Header(_) => String::from("<header>"),
        }
    }

    #[test]
    fn it_opens_on_the_current_smart_model_and_lists_every_provider_with_models() {
        let p = picker();
        assert_eq!(p.providers.len(), 2);
        assert_eq!(highlighted(&p), "gpt-6-astra");
        assert_eq!(p.rows().len(), 2 + 3 + 2);
    }

    #[test]
    fn the_role_switch_moves_to_the_current_fast_model() {
        let mut p = picker();
        p.toggle_role();
        assert_eq!(p.role, Role::Fast);
        assert_eq!(highlighted(&p), "gpt-6.1-sol");
    }

    #[test]
    fn choosing_another_providers_model_keeps_that_providers_other_model() {
        let mut p = picker();
        p.step(10);
        assert_eq!(highlighted(&p), "deepseek-v4-pro");
        p.step(-1);
        assert_eq!(p.choice().unwrap(), ["deepseek", "deepseek-flash", "deepseek-flash"].map(String::from));
        p.toggle_role();
        p.query = "deepseek-v4".into();
        p.refilter();
        assert_eq!(p.choice().unwrap(), ["deepseek", "deepseek-v4-pro", "deepseek-v4-pro"].map(String::from));
    }

    #[test]
    fn typing_filters_models_and_a_provider_name_keeps_all_its_models() {
        let mut p = picker();
        for c in "luna".chars() {
            p.type_char(c);
        }
        assert_eq!(p.rows(), vec![Row::Header(0), Row::Model(0, 0)]);
        p.query = "deepseek".into();
        assert_eq!(p.rows().len(), 3);
    }

    #[test]
    fn an_empty_catalog_opens_nothing() {
        assert!(ModelPicker::from_catalog(&json!({"providers": []})).is_none());
    }
}
