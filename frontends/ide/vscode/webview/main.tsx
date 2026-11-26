import { render } from "preact"

import { App } from "./app"
import { cloudOrigin, Surfaces } from "./cloud"
import "./styles.css"
import "./workspace.css"

// The sidebar panel switches between Kvist Cloud and the local agent; the editor-tab page is the local agent alone.
const panel = document.getElementById("root")?.dataset.layout !== "workspace"

render(panel && cloudOrigin ? <Surfaces><App /></Surfaces> : <App />, document.getElementById("root")!)
